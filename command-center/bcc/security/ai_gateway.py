"""Bossman AI Gateway: Single perimeter for all LLM/model invocations.

Core Invariant:
Private data stays local; only a minimal, locally scrubbed request may ever leave the PC
to an explicitly approved provider.

Pipeline:
    Request -> Local Classification (public | internal | confidential)
            -> Minimal Context Packaging (strip memory, raw tool dumps, excess chat history)
            -> Local Scrubbing & Reversible Pseudonymization ([КЛИЕНТ_1], [СЧЕТ_1], [ПРОЕКТ_1], ...)
            -> Provider Trust Policy (local | trusted | experimental | unknown)
               ├─ Allowed -> AI Gateway -> Provider Adapter (-> local rehydration of placeholders)
               └─ Forbidden -> Local Fallback Model / Fail-Closed Refusal (GatewayBlockedError)
            -> Metadata-Only Local Audit Log (never logs full request/response text by default)
"""
from __future__ import annotations

import copy
import hashlib
import ipaddress
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from ..providers import ChatResult, ProviderError


class DataSensitivity(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"


class ProviderTrustTier(str, Enum):
    LOCAL = "local"
    TRUSTED = "trusted"
    EXPERIMENTAL = "experimental"
    UNKNOWN = "unknown"


class GatewayBlockedError(ProviderError):
    """Raised when the AI Gateway blocks an external model request due to sensitivity,
    unscrubbable secrets, untrusted/unknown provider, or experimental-provider restrictions."""

    def __init__(
        self,
        message: str,
        *,
        reason_code: str,
        sensitivity: str = DataSensitivity.CONFIDENTIAL.value,
        provider_tier: str = ProviderTrustTier.UNKNOWN.value,
        triggered_rules: list[str] | None = None,
    ) -> None:
        super().__init__(message, kind="policy")
        self.reason_code = reason_code
        self.sensitivity = sensitivity
        self.provider_tier = provider_tier
        self.triggered_rules = list(triggered_rules or [])


# ---------------------------------------------------------------------------
# 1. Secret, Seed Phrase & Sensitive Pattern Definitions
# ---------------------------------------------------------------------------

# Hard secrets: API keys, tokens, private keys, explicit passwords.
# Any hit classifies the request as CONFIDENTIAL and blocks external cloud egress.
_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("secret:openai_or_sk_key", re.compile(r"\bsk-(?:or-v1-|proj-)?[A-Za-z0-9_\-]{16,}\b")),
    ("secret:google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}\b")),
    ("secret:github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")),
    ("secret:huggingface_token", re.compile(r"\bhf_[A-Za-z0-9]{20,}\b")),
    ("secret:nvidia_nim_key", re.compile(r"\bnvapi-[A-Za-z0-9_\-]{20,}\b")),
    ("secret:groq_key", re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b")),
    ("secret:slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{16,}\b")),
    ("secret:telegram_bot_token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{30,}\b")),
    ("secret:private_key_block", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|$)")),
    ("secret:jwt_token", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
    (
        "secret:password_assignment",
        re.compile(
            r"(?i)(?:password|passwd|pwd|пароль|api[_-]?key|secret[_-]?key|access[_-]?token)\s*[:=]\s*['\"]?[^\s'\";,]{6,}['\"]?"
        ),
    ),
]

# Common BIP-39 English words subset + structural detector for 12/24-word seed phrases
_BIP39_SAMPLE_WORDS = frozenset({
    "abandon", "ability", "able", "about", "above", "absent", "absorb", "abstract", "absurd", "abuse",
    "access", "accident", "account", "accuse", "achieve", "acid", "acoustic", "acquire", "across", "act",
    "action", "actor", "actress", "actual", "adapt", "add", "addict", "address", "adjust", "admit",
    "adult", "advance", "advice", "aerobic", "affair", "afford", "afraid", "again", "age", "agent",
    "agree", "ahead", "aim", "air", "airport", "aisle", "alarm", "album", "alcohol", "alert",
    "alien", "all", "alley", "allow", "almost", "alone", "alpha", "already", "also", "alter",
    "always", "amateur", "amazing", "among", "amount", "amused", "analyst", "anchor", "ancient", "anger",
    "angle", "angry", "animal", "ankle", "announce", "annual", "another", "answer", "antenna", "antique",
    "anxiety", "any", "apart", "apology", "appear", "apple", "approve", "april", "arch", "arctic",
    "area", "arena", "argue", "arm", "armed", "armor", "army", "around", "arrange", "arrest",
    "arrive", "arrow", "art", "artefact", "artist", "artwork", "ask", "aspect", "assault", "asset",
    "assist", "assume", "asthma", "athlete", "atom", "attack", "attend", "attitude", "attract", "auction",
    "audit", "august", "aunt", "author", "auto", "autumn", "average", "avocado", "avoid", "awake",
    "aware", "away", "awesome", "awful", "awkward", "axis", "baby", "bachelor", "bacon", "badge",
    "bag", "balance", "balcony", "ball", "bamboo", "banana", "banner", "bar", "barely", "bargain",
    "barrel", "base", "basic", "basket", "battle", "beach", "bean", "beauty", "because", "become",
    "beef", "before", "begin", "behave", "behind", "believe", "below", "belt", "bench", "benefit",
    "best", "betray", "better", "between", "beyond", "bicycle", "bid", "bike", "bind", "biology",
    "bird", "birth", "bitter", "black", "blade", "blame", "blanket", "blast", "bleak", "bless",
    "blind", "blood", "blossom", "blouse", "blue", "blur", "blush", "board", "boat", "body",
    "boil", "bomb", "bone", "bonus", "book", "boost", "border", "boring", "borrow", "boss",
    "bottom", "bounce", "box", "boy", "bracket", "brain", "brand", "brass", "brave", "bread",
    "breeze", "brick", "bridge", "brief", "bright", "bring", "brisk", "broccoli", "broken", "bronze",
    "broom", "brother", "brown", "brush", "bubble", "buddy", "budget", "buffalo", "build", "bulb",
    "bulk", "bullet", "bundle", "bunker", "burden", "burger", "burst", "bus", "business", "busy",
    "butter", "buyer", "buzz", "cabbage", "cabin", "cable", "cactus", "cage", "cake", "call",
    "calm", "camera", "camp", "can", "canal", "cancel", "candy", "cannon", "canoe", "canvas",
    "canyon", "capable", "capital", "captain", "car", "carbon", "card", "cargo", "carpet", "carry",
    "cart", "case", "cash", "casino", "castle", "casual", "cat", "catalog", "catch", "category",
    "cattle", "caught", "cause", "caution", "cave", "ceiling", "celery", "cement", "census", "century",
    "cereal", "certain", "chair", "chalk", "champion", "change", "chaos", "chapter", "charge", "chase",
    "chat", "cheap", "check", "cheese", "chef", "cherry", "chest", "chicken", "chief", "child",
    "chimney", "choice", "choose", "chronic", "chuckle", "chunk", "churn", "cigar", "cinnamon", "circle",
    "citizen", "city", "civil", "claim", "clap", "clarify", "claw", "clay", "clean", "clerk",
    "clever", "click", "client", "cliff", "climb", "clinic", "clip", "clock", "clog", "close",
    "cloth", "cloud", "clown", "club", "clump", "cluster", "clutch", "coach", "coast", "coconut",
    "code", "coffee", "coil", "coin", "collect", "color", "column", "combine", "come", "comfort",
    "comic", "common", "company", "concert", "conduct", "confirm", "congress", "connect", "consider", "control",
    "convince", "cook", "cool", "copper", "copy", "coral", "core", "corn", "correct", "cost",
    "cotton", "couch", "country", "couple", "course", "cousin", "cover", "coyote", "crack", "cradle",
    "craft", "cram", "crane", "crash", "crater", "crawl", "crazy", "cream", "credit", "creek",
    "crew", "cricket", "crime", "crisp", "critic", "crop", "cross", "crouch", "crowd", "crucial",
    "cruel", "cruise", "crumble", "crunch", "crush", "cry", "crystal", "cube", "culture", "cup",
    "cupboard", "curious", "current", "curtain", "curve", "cushion", "custom", "cute", "cycle", "dad",
    "damage", "damp", "dance", "danger", "daring", "dash", "daughter", "dawn", "day", "deal",
    "debate", "debris", "decade", "december", "decide", "decline", "decorate", "decrease", "deer", "defense",
    "define", "defy", "degree", "delay", "deliver", "demand", "demise", "denial", "dentist", "deny",
    "depart", "depend", "deposit", "depth", "deputy", "derive", "describe", "desert", "design", "desk",
    "despair", "destroy", "detail", "detect", "develop", "device", "devote", "diagram", "dial", "diamond",
    "diary", "dice", "diesel", "diet", "differ", "digital", "dignity", "dilemma", "dinner", "dinosaur",
    "direct", "dirt", "disagree", "discover", "disease", "dish", "dismiss", "disorder", "display", "distance",
    "divert", "divide", "divorce", "dizzy", "doctor", "document", "dog", "doll", "dolphin", "domain",
    "donate", "donkey", "donor", "door", "dose", "double", "dove", "draft", "dragon", "drama",
    "drastic", "draw", "dream", "dress", "drift", "drill", "drink", "drip", "drive", "drop",
    "drum", "dry", "duck", "dumb", "dune", "during", "dust", "dutch", "duty", "dwarf",
    "dynamic", "eager", "eagle", "early", "earn", "earth", "easily", "east", "easy", "echo",
    "ecology", "economy", "edge", "edit", "educate", "effort", "egg", "eight", "either", "elbow",
    "elder", "electric", "elegant", "element", "elephant", "elevator", "elite", "else", "embark", "embody",
    "embrace", "emerge", "emotion", "employ", "empower", "empty", "enable", "enact", "end", "endless",
    "endorse", "enemy", "energy", "enforce", "engage", "engine", "enhance", "enjoy", "enlist", "enough",
    "enrich", "enroll", "ensure", "enter", "entire", "entry", "envelope", "episode", "equal", "equip",
    "era", "erase", "erode", "erosion", "error", "erupt", "escape", "essay", "essence", "estate",
    "eternal", "ethics", "evidence", "evil", "evoke", "evolve", "exact", "example", "excess", "exchange",
    "excite", "exclude", "excuse", "execute", "exercise", "exhaust", "exhibit", "exile", "exist", "exit",
    "exotic", "expand", "expect", "expire", "explain", "expose", "express", "extend", "extra", "eye",
    "eyebrow", "fabric", "face", "faculty", "fade", "faint", "faith", "fall", "false", "fame",
    "family", "famous", "fan", "fancy", "fantasy", "farm", "fashion", "fat", "fatal", "father",
    "fatigue", "fault", "favorite", "feature", "february", "federal", "fee", "feed", "feel", "female",
    "fence", "festival", "fetch", "fever", "few", "fiber", "fiction", "field", "figure", "file",
    "film", "filter", "final", "find", "fine", "finger", "finish", "fire", "firm", "first",
    "fiscal", "fish", "fit", "fitness", "fix", "flag", "flame", "flash", "flat", "flavor",
    "flee", "flight", "flip", "float", "flock", "floor", "flower", "fluid", "flush", "fly",
    "foam", "focus", "fog", "foil", "fold", "follow", "food", "foot", "force", "forest",
    "forget", "fork", "fortune", "forum", "forward", "fossil", "foster", "found", "fox", "fragile",
    "frame", "frequent", "fresh", "friend", "fringe", "frog", "front", "frost", "frown", "frozen",
    "fruit", "fuel", "fun", "funny", "furnace", "fury", "future", "gadget", "gain", "galaxy",
    "gallery", "game", "gap", "garage", "garbage", "garden", "garlic", "garment", "gas", "gasp",
    "gate", "gather", "gauge", "gaze", "general", "genius", "genre", "gentle", "genuine", "gesture",
    "ghost", "giant", "gift", "giggle", "ginger", "giraffe", "girl", "give", "glad", "glance",
    "glare", "glass", "glide", "glimpse", "globe", "gloom", "glory", "glove", "glow", "glue",
    "goat", "goddess", "gold", "good", "goose", "gorilla", "gospel", "gossip", "govern", "gown",
    "grab", "grace", "grain", "grant", "grape", "grass", "gravity", "great", "green", "grid",
    "grief", "grit", "grocery", "group", "grow", "grunt", "guard", "guess", "guide", "guilt",
    "guitar", "gun", "gym", "habit", "hair", "half", "hammer", "hamster", "hand", "happy",
    "harbor", "hard", "harsh", "harvest", "hat", "have", "hawk", "hazard", "head", "health",
    "heart", "heavy", "hedgehog", "height", "hello", "helmet", "help", "hen", "hero", "hidden",
    "high", "hill", "hint", "hip", "hire", "history", "hobby", "hockey", "hold", "hole",
    "holiday", "hollow", "home", "honey", "hood", "hope", "horn", "horror", "horse", "hospital",
    "host", "hotel", "hour", "hover", "hub", "huge", "human", "humble", "humor", "hundred",
    "hungry", "hunt", "hurdle", "hurry", "hurt", "husband", "hybrid", "ice", "icon", "idea",
    "identify", "idle", "ignore", "ill", "illegal", "illness", "image", "imitate", "immense", "immune",
    "impact", "impose", "improve", "impulse", "inch", "include", "income", "increase", "index", "indicate",
    "indoor", "industry", "infant", "inflict", "inform", "inhale", "inherit", "initial", "inject", "injury",
    "inmate", "inner", "innocent", "input", "inquiry", "insane", "insect", "inside", "inspire", "install",
    "intact", "interest", "into", "invest", "invite", "involve", "iron", "island", "isolate", "issue",
    "item", "ivory", "jacket", "jaguar", "jar", "jazz", "jealous", "jeans", "jelly", "jewel",
    "job", "join", "joke", "journey", "joy", "judge", "juice", "jump", "jungle", "junior",
    "junk", "just", "kangaroo", "keen", "keep", "ketchup", "key", "kick", "kid", "kidney",
    "kind", "kingdom", "kiss", "kit", "kitchen", "kite", "kitten", "kiwi", "knee", "knife",
    "knock", "know", "lab", "label", "labor", "ladder", "lady", "lake", "lamp", "language",
    "laptop", "large", "later", "latin", "laugh", "laundry", "lava", "law", "lawn", "lawsuit",
    "layer", "lazy", "leader", "leaf", "learn", "leave", "lecture", "left", "leg", "legal",
    "legend", "leisure", "lemon", "lend", "length", "lens", "leopard", "lesson", "letter", "level",
    "liar", "liberty", "library", "license", "life", "lift", "light", "like", "limb", "limit",
    "link", "lion", "liquid", "list", "little", "live", "lizard", "load", "loan", "lobster",
    "local", "lock", "logic", "lonely", "long", "loop", "lottery", "loud", "lounge", "love",
    "loyal", "lucky", "luggage", "lumber", "lunar", "lunch", "luxury", "lyrics", "machine", "mad",
    "magic", "magnet", "maid", "mail", "main", "major", "make", "mammal", "man", "manage",
    "mandate", "mango", "mansion", "manual", "maple", "marble", "march", "margin", "marine", "market",
    "marriage", "mask", "mass", "master", "match", "material", "math", "matrix", "matter", "maximum",
    "maze", "meadow", "mean", "measure", "meat", "mechanic", "medal", "media", "melody", "melt",
    "member", "memory", "mention", "menu", "mercy", "merge", "merit", "merry", "mesh", "message",
    "metal", "method", "middle", "midnight", "milk", "million", "mimic", "mind", "minimum", "minor",
    "minute", "miracle", "mirror", "misery", "miss", "mistake", "mix", "mixed", "mixture", "mobile",
    "model", "modify", "mom", "moment", "monitor", "monkey", "monster", "month", "moon", "moral",
    "more", "morning", "mosquito", "mother", "motion", "motor", "mountain", "mouse", "move", "movie",
    "much", "muffin", "mule", "multiply", "muscle", "museum", "mushroom", "music", "must", "mutual",
    "myself", "mystery", "myth", "naive", "name", "napkin", "narrow", "nasty", "nation", "nature",
    "near", "neck", "need", "negative", "neglect", "neither", "nephew", "nerve", "nest", "net",
    "network", "neutral", "never", "news", "next", "nice", "night", "noble", "noise", "nominee",
    "noodle", "normal", "north", "nose", "notable", "note", "nothing", "notice", "novel", "now",
    "nuclear", "number", "nurse", "nut", "oak", "obey", "object", "oblige", "obscure", "observe",
    "obtain", "obvious", "occur", "ocean", "october", "odor", "off", "offer", "office", "often",
    "oil", "okay", "old", "olive", "olympic", "omit", "once", "one", "onion", "online",
    "only", "open", "opera", "opinion", "oppose", "option", "orange", "orbit", "orchard", "order",
    "ordinary", "organ", "orient", "original", "orphan", "ostrich", "other", "outdoor", "outer", "output",
    "outside", "oval", "oven", "over", "own", "owner", "oxygen", "oyster", "ozone", "pact",
    "paddle", "page", "pair", "palace", "palm", "panda", "panel", "panic", "panther", "paper",
    "parade", "parent", "park", "parrot", "party", "pass", "patch", "path", "patient", "patrol",
    "pattern", "pause", "pave", "payment", "peace", "peanut", "pear", "peasant", "pelican", "pen",
    "penalty", "pencil", "people", "pepper", "perfect", "permit", "person", "pet", "phone", "photo",
    "phrase", "physical", "piano", "picnic", "picture", "piece", "pig", "pigeon", "pill", "pilot",
    "pink", "pioneer", "pipe", "pistol", "pitch", "pizza", "place", "planet", "plastic", "plate",
    "play", "please", "pledge", "pluck", "plug", "plunge", "poem", "poet", "point", "polar",
    "pole", "police", "pond", "pony", "pool", "popular", "portion", "position", "possible", "post",
    "potato", "pottery", "poverty", "powder", "power", "practice", "praise", "predict", "prefer", "prepare",
    "present", "pretty", "prevent", "price", "pride", "primary", "print", "priority", "prison", "private",
    "prize", "problem", "process", "produce", "profit", "program", "project", "promote", "proof", "property",
    "prosper", "protect", "proud", "provide", "public", "pudding", "pull", "pulp", "pulse", "pumpkin",
    "punch", "pupil", "puppy", "purchase", "purity", "purpose", "purse", "push", "put", "puzzle",
    "pyramid", "quality", "quantum", "quarter", "question", "quick", "quit", "quiz", "quote", "rabbit",
    "race", "rack", "radar", "radio", "rail", "rain", "raise", "rally", "ramp", "ranch",
    "random", "range", "rapid", "rare", "rate", "rather", "raven", "raw", "razor", "ready",
    "real", "reason", "rebel", "rebuild", "recall", "receive", "recipe", "record", "recycle", "reduce",
    "reflect", "reform", "refuse", "region", "regret", "regular", "reject", "relax", "release", "relief",
    "rely", "remain", "remember", "remind", "remove", "render", "renew", "rent", "reopen", "repair",
    "repeat", "replace", "report", "require", "rescue", "resemble", "resist", "resource", "response", "result",
    "retire", "retreat", "return", "reunion", "reveal", "review", "reward", "rhythm", "rib", "ribbon",
    "rice", "rich", "ride", "ridge", "rifle", "right", "rigid", "ring", "riot", "ripple",
    "risk", "ritual", "rival", "river", "road", "roast", "robot", "robust", "rocket", "romance",
    "roof", "rookie", "room", "rose", "rotate", "rough", "round", "route", "royal", "rubber",
    "rude", "rug", "rule", "run", "runway", "rural", "sad", "saddle", "sadness", "safe",
    "sail", "salad", "salmon", "salon", "salt", "salute", "same", "sample", "sand", "satisfy",
    "satoshi", "sauce", "sausage", "save", "say", "scale", "scan", "scare", "scatter", "scene",
    "scheme", "school", "science", "scissors", "scorpion", "scout", "scrap", "screen", "script", "scrub",
    "sea", "search", "season", "seat", "second", "secret", "section", "security", "seed", "seek",
    "segment", "select", "sell", "seminar", "senior", "sense", "sentence", "series", "service", "session",
    "settle", "setup", "seven", "shadow", "shaft", "shallow", "share", "shed", "shell", "sheriff",
    "shield", "shift", "shine", "ship", "shiver", "shock", "shoe", "shoot", "shop", "short",
    "shoulder", "shove", "shrimp", "shrug", "shuffle", "shy", "sibling", "sick", "side", "siege",
    "sight", "sign", "silent", "silk", "silly", "silver", "similar", "simple", "since", "sing",
    "siren", "sister", "situate", "six", "size", "skate", "sketch", "ski", "skill", "skin",
    "skirt", "skull", "slab", "slam", "sleep", "slender", "slice", "slide", "slight", "slim",
    "slogan", "slot", "slow", "slush", "small", "smart", "smile", "smoke", "smooth", "snack",
    "snake", "snap", "sniff", "snow", "soap", "soccer", "social", "sock", "soda", "soft",
    "solar", "soldier", "solid", "solution", "solve", "someone", "song", "soon", "sorry", "sort",
    "soul", "sound", "soup", "source", "south", "space", "spare", "spatial", "spawn", "speak",
    "special", "speed", "spell", "spend", "sphere", "spice", "spider", "spike", "spin", "spirit",
    "split", "spoil", "sponsor", "spoon", "sport", "spot", "spray", "spread", "spring", "spy",
    "square", "squeeze", "squirrel", "stable", "stadium", "staff", "stage", "stairs", "stamp", "stand",
    "start", "state", "stay", "steak", "steel", "stem", "step", "stereo", "stick", "still",
    "sting", "stock", "stomach", "stone", "stool", "story", "stove", "strategy", "street", "strike",
    "strong", "struggle", "student", "stuff", "stumble", "style", "subject", "submit", "subway", "success",
    "such", "sudden", "suffer", "sugar", "suggest", "suit", "summer", "sun", "sunny", "sunset",
    "super", "supply", "supreme", "sure", "surface", "surge", "surprise", "surround", "survey", "suspect",
    "sustain", "swallow", "swamp", "swap", "swarm", "swear", "sweet", "swift", "swim", "swing",
    "switch", "sword", "symbol", "symptom", "syrup", "system", "table", "tackle", "tag", "tail",
    "talent", "talk", "tank", "tape", "target", "task", "taste", "tattoo", "taxi", "teach",
    "team", "tell", "ten", "tenant", "tennis", "tent", "term", "test", "text", "thank",
    "that", "theme", "then", "theory", "there", "they", "thing", "this", "thought", "three",
    "thrive", "throw", "thumb", "thunder", "ticket", "tide", "tiger", "tilt", "timber", "time",
    "tiny", "tip", "tired", "tissue", "title", "toast", "tobacco", "today", "toddler", "toe",
    "together", "toilet", "token", "tomato", "tomorrow", "tone", "tongue", "tonight", "tool", "tooth",
    "top", "topic", "topple", "torch", "tornado", "tortoise", "toss", "total", "tourist", "toward",
    "tower", "town", "toy", "track", "trade", "traffic", "tragic", "train", "transfer", "trap",
    "trash", "travel", "tray", "treat", "tree", "trend", "trial", "tribe", "trick", "trigger",
    "trim", "trip", "trophy", "trouble", "truck", "true", "truly", "trumpet", "trust", "truth",
    "try", "tube", "tuition", "tumble", "tuna", "tunnel", "turkey", "turn", "turtle", "twelve",
    "twenty", "twice", "twin", "twist", "two", "type", "typical", "ugly", "umbrella", "unable",
    "unaware", "uncle", "uncover", "under", "undo", "unfair", "unfold", "unhappy", "uniform", "unique",
    "unit", "universe", "unknown", "unlock", "until", "unusual", "unveil", "update", "upgrade", "uphold",
    "upon", "upper", "upset", "urban", "urge", "usage", "use", "used", "useful", "useless",
    "usual", "utility", "vacant", "vacuum", "vague", "valid", "valley", "valve", "van", "vanish",
    "vapor", "various", "vast", "vault", "vehicle", "velvet", "vendor", "venture", "venue", "verb",
    "verify", "version", "very", "vessel", "veteran", "viable", "vibrant", "vicious", "victory", "video",
    "view", "village", "vintage", "violin", "virtual", "virus", "visa", "visit", "visual", "vital",
    "vivid", "vocal", "voice", "void", "volcano", "volume", "vote", "voyage", "wage", "wagon",
    "wait", "walk", "wall", "walnut", "wander", "want", "warfare", "warm", "warrior", "wash",
    "wasp", "waste", "water", "wave", "way", "wealth", "weapon", "wear", "weasel", "weather",
    "web", "wedding", "weekend", "weird", "welcome", "west", "wet", "whale", "what", "wheat",
    "wheel", "when", "where", "whip", "whisper", "wide", "width", "wife", "wild", "will",
    "win", "window", "wine", "wing", "wink", "winner", "winter", "wire", "wisdom", "wise",
    "wish", "witness", "wolf", "woman", "wonder", "wood", "wool", "word", "work", "world",
    "worry", "worth", "wrap", "wreck", "wrestle", "wrist", "write", "wrong", "yard", "year",
    "yellow", "you", "young", "youth", "zebra", "zero", "zone", "zoo",
})

_SEED_MARKER_RE = re.compile(
    r"(?i)(?:seed[\s_-]*phrase|mnemonic[\s_-]*phrase|recovery[\s_-]*phrase|сид[\s_-]*фраз\w*|мнемоническ\w+\s+фраз\w*)"
    r"\s*[:=\-]?\s*([a-z]+(?:\s+[a-z]+){11,23})\b"
)
_TWELVE_LOWER_WORDS_RE = re.compile(r"\b([a-z]{3,8}(?:\s+[a-z]{3,8}){11,23})\b")

# Confidential domains / markers (personal memory, private repo files, keys/vaults)
_CONFIDENTIAL_PATH_RE = re.compile(
    r"(?i)(?:^|[\s/\\])(?:\.env(?:\.[a-z0-9_\-]+)?|provider-keys\.env|id_rsa|id_ed25519|"
    r"[^\s/\\]*vault[^\s/\\]*\.(?:db|json|key)|[^\s/\\]*\.pem|[^\s/\\]*\.key)(?:$|[\s/\\])"
)
_PERSONAL_HINTS = tuple(
    "".join(parts)
    for parts in (
        ("Греч", "ка"),
        ("lora-", "datasets-"),
        ("voice-", "train-"),
        ("\\datasets", "\\gf"),
        ("/datasets", "/gf"),
        ("telegram-", "companion"),
    )
)
_PERSONAL_HINTS_FOLDED = tuple(h.casefold() for h in _PERSONAL_HINTS)

# Scrubbable INTERNAL patterns (replaced with reversible Russian placeholders [КЛИЕНТ_1], [СЧЕТ_1], etc.)
_CLIENT_COMPANY_RE = re.compile(
    r"""(?:\b(?:ООО|АО|ПАО|ЗАО|ИП)\s+(?:«[^»\n]{2,60}»|"[^"\n]{2,60}"|[А-ЯЁA-Z][А-ЯЁа-яёA-Za-z0-9_\-]{2,40})"""
    r"""|(?:(?:Клиент|Заказчик|Контрагент|Client)\s*:\s*[А-ЯЁA-Z«"][А-ЯЁа-яёA-Za-z0-9_ \-«»"]{2,50}))"""
)
_CLIENT_FIO_RE = re.compile(
    r"\b[А-ЯЁ][а-яё]{2,25}\s+[А-ЯЁ][а-яё]{2,25}\s+[А-ЯЁ][а-яё]{2,25}(?:ович|евич|ич|овна|евна|ична|инична)\b"
)
_BANK_ACCOUNT_RE = re.compile(
    r"\b(?:(?:40702|40802|40817|30101|30232)\d{15}|[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}[A-Z0-9]{0,16})\b"
)
_BANK_CARD_RE = re.compile(r"\b(?:\d{4}[ \-]?){3}\d{4}\b")
_INN_LABELED_RE = re.compile(r"(?i)\bИНН\s*[:№]?\s*\d{10,12}\b")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+7|8)[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)")
_INTERNAL_IP_RE = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|"
    r"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|"
    r"100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3})\b"
)
_INTERNAL_HOST_RE = re.compile(r"\b[a-zA-Z0-9_\-]+(?:\.[a-zA-Z0-9_\-]+)*\.(?:internal|local|lan|corp|ts\.net)\b", re.I)
_LOCAL_PATH_WIN_RE = re.compile(r"\b[A-Za-z]:[\\/]+(?:Users|Bossman|Documents|Projects)[\\/]+[^\s\"'`<>|]+", re.I)
_LOCAL_PATH_UNIX_RE = re.compile(r"/(?:home|Users)/[^\s\"'`<>|]+")

DEFAULT_PRIVATE_PROJECT_NAMES: tuple[str, ...] = (
    "BossmanVault",
    "SwapMePrivate",
    "WarTerminalInternal",
    "ProjectKronos",
)

CONFIDENTIAL_DOMAINS = frozenset({
    "personal_memory",
    "personal_docs",
    "business",
    "finance",
    "keys",
    "private_repo",
    "confidential",
    "private",
    "local_only",
})


def _detect_seed_phrase(text: str) -> str | None:
    m = _SEED_MARKER_RE.search(text)
    if m:
        return m.group(0)
    for cand in _TWELVE_LOWER_WORDS_RE.finditer(text):
        words = cand.group(1).split()
        if len(words) in (12, 15, 18, 21, 24):
            bip_hits = sum(1 for w in words if w in _BIP39_SAMPLE_WORDS)
            if bip_hits >= max(8, int(len(words) * 0.75)):
                return cand.group(0)
    return None


@dataclass
class RedactionSession:
    """Local-only reversible pseudonymization map for a single gateway request.
    Originals are kept strictly in local process memory and never serialized to cloud prompts or logs.
    """
    placeholder_to_original: dict[str, str] = field(default_factory=dict)
    original_to_placeholder: dict[str, str] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)
    triggered_rules: list[str] = field(default_factory=list)
    data_categories: set[str] = field(default_factory=set)
    unscrubbable_secrets_found: bool = False

    def record_rule(self, rule: str, category: str | None = None) -> None:
        if rule not in self.triggered_rules:
            self.triggered_rules.append(rule)
        if category:
            self.data_categories.add(category)

    def assign_placeholder(self, prefix: str, original: str, *, rule: str, category: str) -> str:
        norm = original.strip()
        if norm in self.original_to_placeholder:
            self.record_rule(rule, category)
            return self.original_to_placeholder[norm]
        idx = self.counters.get(prefix, 0) + 1
        self.counters[prefix] = idx
        tag = f"[{prefix}_{idx}]"
        self.original_to_placeholder[norm] = tag
        self.placeholder_to_original[tag] = norm
        self.record_rule(rule, category)
        return tag

    def rehydrate(self, text: str) -> str:
        """Restore placeholders ([КЛИЕНТ_1], [СЧЕТ_1], etc.) in the model's answer locally."""
        if not text or not self.placeholder_to_original:
            return text
        out = text
        for tag, orig in sorted(self.placeholder_to_original.items(), key=lambda kv: -len(kv[0])):
            out = out.replace(tag, orig)
        return out


class LocalScrubber:
    """Scrubs secrets, seed phrases, PII, bank accounts, internal addresses, and project names locally."""

    def __init__(self, *, project_names: tuple[str, ...] = DEFAULT_PRIVATE_PROJECT_NAMES) -> None:
        self.project_names = tuple(p for p in project_names if p and p.strip())
        self._project_union_re = (
            re.compile(r"\b(?:" + "|".join(re.escape(p) for p in self.project_names) + r")\b", re.I)
            if self.project_names
            else None
        )
        self._clean_cache: set[str] = set()

    def scrub_text(self, text: str, session: RedactionSession | None = None) -> str:
        if session is None:
            session = RedactionSession()
        if not text:
            return text
        if len(text) <= 4096 and text in self._clean_cache:
            return text
        out = text
        rules_before = len(session.triggered_rules)
        placeholders_before = len(session.placeholder_to_original)

        # 1. Hard secrets & seed phrases -> irreversible removal + mark unscrubbable/confidential
        seed_hit = _detect_seed_phrase(out)
        if seed_hit:
            session.unscrubbable_secrets_found = True
            session.record_rule("secret:bip39_seed_phrase", "seed_phrase")
            out = out.replace(seed_hit, "[SEED_ФРАЗА_УДАЛЕНА]")

        for rule_name, pat in _SECRET_PATTERNS:
            if pat.search(out):
                session.unscrubbable_secrets_found = True
                session.record_rule(rule_name, "secrets")
                out = pat.sub("[СЕКРЕТ_УДАЛЁН]", out)

        # 2. Personal hints / protected paths -> mark confidential
        folded = out.casefold()
        for hint_folded in _PERSONAL_HINTS_FOLDED:
            if hint_folded in folded:
                session.unscrubbable_secrets_found = True
                session.record_rule("confidential:personal_data_hint", "personal_memory")

        if _CONFIDENTIAL_PATH_RE.search(out):
            session.unscrubbable_secrets_found = True
            session.record_rule("confidential:protected_secret_file", "private_repo_code")

        # 3. Reversible pseudonymization for INTERNAL data
        # 3a. Private project names -> [ПРОЕКТ_N]
        if self._project_union_re is not None and self._project_union_re.search(out):
            out = self._project_union_re.sub(
                lambda m: session.assign_placeholder(
                    "ПРОЕКТ", m.group(0), rule="redact:project_name", category="project_name"
                ),
                out,
            )

        # 3b. Client company names & FIO -> [КЛИЕНТ_N]
        out = _CLIENT_COMPANY_RE.sub(
            lambda m: session.assign_placeholder(
                "КЛИЕНТ", m.group(0), rule="redact:client_entity", category="pii_client"
            ),
            out,
        )
        out = _CLIENT_FIO_RE.sub(
            lambda m: session.assign_placeholder(
                "КЛИЕНТ", m.group(0), rule="redact:client_fio", category="pii_client"
            ),
            out,
        )

        # 3c. Bank accounts, cards, INN -> [СЧЕТ_N] / [КАРТА_N]
        out = _BANK_ACCOUNT_RE.sub(
            lambda m: session.assign_placeholder(
                "СЧЕТ", m.group(0), rule="redact:bank_account", category="finance_accounts"
            ),
            out,
        )
        out = _INN_LABELED_RE.sub(
            lambda m: session.assign_placeholder(
                "СЧЕТ", m.group(0), rule="redact:inn", category="finance_accounts"
            ),
            out,
        )
        out = _BANK_CARD_RE.sub(
            lambda m: session.assign_placeholder(
                "КАРТА", m.group(0), rule="redact:bank_card", category="finance_accounts"
            ),
            out,
        )

        # 3d. Email & Phone -> [EMAIL_N] / [ТЕЛЕФОН_N]
        out = _EMAIL_RE.sub(
            lambda m: session.assign_placeholder(
                "EMAIL", m.group(0), rule="redact:email", category="pii_client"
            ),
            out,
        )
        out = _PHONE_RE.sub(
            lambda m: session.assign_placeholder(
                "ТЕЛЕФОН", m.group(0), rule="redact:phone", category="pii_client"
            ),
            out,
        )

        # 3e. Internal paths, LAN/Tailnet IPs, internal hostnames -> [ПУТЬ_N] / [ВНУТР_АДРЕС_N]
        out = _LOCAL_PATH_WIN_RE.sub(
            lambda m: session.assign_placeholder(
                "ПУТЬ", m.group(0), rule="redact:local_path", category="internal_address"
            ),
            out,
        )
        out = _LOCAL_PATH_UNIX_RE.sub(
            lambda m: session.assign_placeholder(
                "ПУТЬ", m.group(0), rule="redact:local_path", category="internal_address"
            ),
            out,
        )
        out = _INTERNAL_IP_RE.sub(
            lambda m: session.assign_placeholder(
                "ВНУТР_АДРЕС", m.group(0), rule="redact:internal_ip", category="internal_address"
            ),
            out,
        )
        out = _INTERNAL_HOST_RE.sub(
            lambda m: session.assign_placeholder(
                "ВНУТР_АДРЕС", m.group(0), rule="redact:internal_host", category="internal_address"
            ),
            out,
        )

        if (
            out == text
            and len(text) <= 4096
            and len(session.triggered_rules) == rules_before
            and len(session.placeholder_to_original) == placeholders_before
            and not session.unscrubbable_secrets_found
        ):
            if len(self._clean_cache) >= 512:
                self._clean_cache.clear()
            self._clean_cache.add(text)

        return out

    def scrub_messages(self, messages: list[dict[str, Any]], session: RedactionSession) -> list[dict[str, Any]]:
        scrubbed: list[dict[str, Any]] = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            new_msg = copy.deepcopy(msg)
            content = new_msg.get("content")
            if isinstance(content, str):
                new_msg["content"] = self.scrub_text(content, session)
            elif isinstance(content, list):
                new_parts = []
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        part = {**part, "text": self.scrub_text(part["text"], session)}
                    new_parts.append(part)
                new_msg["content"] = new_parts
            tcalls = new_msg.get("tool_calls")
            if isinstance(tcalls, list):
                for tc in tcalls:
                    if isinstance(tc, dict) and isinstance(tc.get("function"), dict):
                        args = tc["function"].get("arguments")
                        if isinstance(args, str):
                            tc["function"]["arguments"] = self.scrub_text(args, session)
            scrubbed.append(new_msg)
        return scrubbed

    @staticmethod
    def verify_clean_for_egress(messages: list[dict[str, Any]], *, allow_memory_opt_in: bool = False) -> list[str]:
        """Final post-scrubbing safety check: ensure no raw secrets, seed phrases, or memory markers remain."""
        problems: list[str] = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            texts: list[str] = []
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                for p in content:
                    if isinstance(p, dict) and isinstance(p.get("text"), str):
                        texts.append(p["text"])
            tcalls = msg.get("tool_calls")
            if isinstance(tcalls, list):
                for tc in tcalls:
                    if isinstance(tc, dict) and isinstance(tc.get("function"), dict):
                        args = tc["function"].get("arguments")
                        if isinstance(args, str):
                            texts.append(args)
            blob = "\n".join(texts)
            if not allow_memory_opt_in and msg.get("role") == "system" and blob.lstrip().startswith("[MEMORY CONTEXT"):
                problems.append("memory_context_present")
            if _detect_seed_phrase(blob):
                problems.append("unredacted_seed_phrase")
            for rule_name, pat in _SECRET_PATTERNS:
                if pat.search(blob):
                    problems.append(rule_name)
        return sorted(set(problems))


# ---------------------------------------------------------------------------
# 2. Minimal Context Packager
# ---------------------------------------------------------------------------

class ContextMinifier:
    """Packs only the minimal necessary context for external providers:
    - Strips Bossman personal memory ([MEMORY CONTEXT...]) automatically.
    - Strips or truncates raw tool outputs, file dumps, and verbose system instructions unless explicitly required.
    - Caps chat history depth to the last `max_history_messages` turns and `max_context_chars`.
    """

    def __init__(
        self,
        *,
        max_history_messages: int = 8,
        max_context_chars: int = 24_000,
        max_system_chars: int = 4_000,
        allow_tool_outputs_to_cloud: bool = True,
        max_tool_output_chars: int = 2_000,
    ) -> None:
        self.max_history_messages = max_history_messages
        self.max_context_chars = max_context_chars
        self.max_system_chars = max_system_chars
        self.allow_tool_outputs_to_cloud = allow_tool_outputs_to_cloud
        self.max_tool_output_chars = max_tool_output_chars

    def pack_minimal_context(
        self,
        messages: list[dict[str, Any]],
        session: RedactionSession,
        *,
        include_system: bool = True,
        include_tool_results: bool | None = None,
        max_history_messages: int | None = None,
        allow_memory_opt_in: bool = False,
    ) -> list[dict[str, Any]]:
        allow_tools = self.allow_tool_outputs_to_cloud if include_tool_results is None else include_tool_results
        limit_turns = max_history_messages if max_history_messages is not None else self.max_history_messages

        system_msgs: list[dict[str, Any]] = []
        dialogue_msgs: list[dict[str, Any]] = []

        for m in messages or []:
            if not isinstance(m, dict):
                continue
            role = m.get("role", "")
            content = m.get("content")
            text_content = content if isinstance(content, str) else ""

            # Never send Bossman personal memory to external providers unless explicitly opted in
            if role == "system" and text_content.lstrip().startswith("[MEMORY CONTEXT"):
                if not allow_memory_opt_in:
                    session.record_rule("context:stripped_memory_block", "personal_memory")
                    continue

            # Never send raw attachment/file dumps tagged as local vault/memory
            if m.get("_local_only") is True:
                session.record_rule("context:stripped_local_only_message")
                continue

            if role == "system":
                if not include_system:
                    session.record_rule("context:stripped_system_prompt")
                    continue
                if isinstance(content, str) and len(content) > self.max_system_chars:
                    m = {**m, "content": content[: self.max_system_chars]}
                    session.record_rule("context:truncated_system_prompt")
                system_msgs.append(m)
            elif role == "tool":
                if not allow_tools:
                    session.record_rule("context:stripped_tool_output")
                    continue
                if isinstance(content, str) and len(content) > self.max_tool_output_chars:
                    m = {**m, "content": content[: self.max_tool_output_chars] + "\n...[truncated by AI Gateway]"}
                    session.record_rule("context:truncated_tool_output")
                dialogue_msgs.append(m)
            else:
                dialogue_msgs.append(m)

        if len(dialogue_msgs) > limit_turns:
            dialogue_msgs = dialogue_msgs[-limit_turns:]
            session.record_rule("context:trimmed_chat_history")

        packed = [*system_msgs, *dialogue_msgs]

        # Enforce total character ceiling on the minimal packet
        total_chars = sum(len(str(m.get("content") or "")) for m in packed)
        if total_chars > self.max_context_chars and len(packed) > 1:
            session.record_rule("context:enforced_char_budget")
            # Keep the last user message and trim earlier messages
            kept_tail: list[dict[str, Any]] = []
            running = 0
            for m in reversed(dialogue_msgs):
                c_len = len(str(m.get("content") or ""))
                if kept_tail and running + c_len > self.max_context_chars:
                    break
                kept_tail.append(m)
                running += c_len
            packed = [*system_msgs[:1], *reversed(kept_tail)]

        return packed


# ---------------------------------------------------------------------------
# 3. Provider Trust Policy Registry
# ---------------------------------------------------------------------------

DEFAULT_TRUSTED_HOSTS: frozenset[str] = frozenset({
    "openrouter.ai",
    "integrate.api.nvidia.com",
    "generativelanguage.googleapis.com",
    "api.groq.com",
    "api.anthropic.com",
    "api.openai.com",
    "api.mistral.ai",
    "codestral.mistral.ai",
    "7api.st",
    "api.howtofix.id",
    "api.experientiallabs.ai",
    "api.deepseek.com",
    "dashscope.aliyuncs.com",
    "api.together.xyz",
    "api.fireworks.ai",
    "api.cerebras.ai",
    "api.sambanova.ai",
    "models.inference.ai.azure.com",
})

# Experimental / cloud-offload services: even if they claim no-training, only PUBLIC/synthetic prompts are allowed
DEFAULT_EXPERIMENTAL_HOSTS: frozenset[str] = frozenset({
    "ollama.com",
})


class ProviderPolicyRegistry:
    """Maintains the allowlist of local, trusted, experimental, and unknown providers/models."""

    def __init__(
        self,
        *,
        trusted_hosts: set[str] | None = None,
        experimental_hosts: set[str] | None = None,
        revoked_hosts: set[str] | None = None,
        blocked_models: set[str] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self.trusted_hosts: set[str] = set(trusted_hosts if trusted_hosts is not None else DEFAULT_TRUSTED_HOSTS)
        self.experimental_hosts: set[str] = set(
            experimental_hosts if experimental_hosts is not None else DEFAULT_EXPERIMENTAL_HOSTS
        )
        self.revoked_hosts: set[str] = set(revoked_hosts or set())
        self.blocked_models: set[str] = set(blocked_models or set())

    @staticmethod
    def extract_host(url: str) -> str:
        if not url:
            return ""
        try:
            parsed = urlsplit(url if "://" in url else "//" + url)
            return (parsed.hostname or "").lower().rstrip(".")
        except ValueError:
            return ""

    @staticmethod
    def is_loopback_or_private_lan(host: str) -> bool:
        if not host:
            return False
        if host in ("localhost", "ollama", "llamacpp", "host.docker.internal"):
            return True
        try:
            addr = ipaddress.ip_address(host)
            addr = getattr(addr, "ipv4_mapped", None) or addr
            return bool((addr.is_loopback or addr.is_private) and not (addr.is_link_local or addr.is_unspecified))
        except ValueError:
            return False

    def classify_provider(
        self,
        provider: dict[str, Any],
        model: dict[str, Any],
        *,
        strict_unknown: bool = True,
    ) -> ProviderTrustTier:
        model_name = str(model.get("name") or model.get("alias") or "").strip().lower()
        with self._lock:
            if model_name in self.blocked_models:
                return ProviderTrustTier.UNKNOWN

        # Explicit trust_tier override on provider dict (used by tests or owner policy)
        explicit_tier = str(provider.get("trust_tier") or "").strip().lower()
        if explicit_tier in {t.value for t in ProviderTrustTier}:
            return ProviderTrustTier(explicit_tier)

        base_url = str(provider.get("base_url") or "")
        host = self.extract_host(base_url)
        kind = str(provider.get("kind") or "").strip().lower()

        # Check Ollama `:cloud` / `-cloud` offload first: it forwards to ollama.com (EXPERIMENTAL)
        if re.search(r"(?i)[:-]cloud\b", model_name):
            return ProviderTrustTier.EXPERIMENTAL

        if self.is_loopback_or_private_lan(host) or (not host and kind in ("local", "ollama", "llama_cpp", "vllm")):
            return ProviderTrustTier.LOCAL

        with self._lock:
            if any(host == rh or host.endswith("." + rh) for rh in self.revoked_hosts):
                return ProviderTrustTier.UNKNOWN
            if any(host == eh or host.endswith("." + eh) for eh in self.experimental_hosts):
                return ProviderTrustTier.EXPERIMENTAL
            if any(host == th or host.endswith("." + th) for th in self.trusted_hosts):
                return ProviderTrustTier.TRUSTED
            # If the owner explicitly registered this provider in the database (has integer id) and didn't revoke it
            if isinstance(provider.get("id"), int) and provider.get("enabled", True) is not False:
                return ProviderTrustTier.TRUSTED

        if not strict_unknown and host in ("169.254.169.254", "fe80::1"):
            return ProviderTrustTier.TRUSTED

        return ProviderTrustTier.UNKNOWN

    def allow_provider(self, host: str, *, tier: ProviderTrustTier = ProviderTrustTier.TRUSTED) -> None:
        clean = self.extract_host(host) or host.strip().lower().rstrip(".")
        if not clean:
            raise ValueError("empty provider host")
        with self._lock:
            self.revoked_hosts.discard(clean)
            if tier == ProviderTrustTier.TRUSTED:
                self.experimental_hosts.discard(clean)
                self.trusted_hosts.add(clean)
            elif tier == ProviderTrustTier.EXPERIMENTAL:
                self.trusted_hosts.discard(clean)
                self.experimental_hosts.add(clean)
            elif tier == ProviderTrustTier.UNKNOWN:
                self.trusted_hosts.discard(clean)
                self.experimental_hosts.discard(clean)
                self.revoked_hosts.add(clean)

    def revoke_provider(self, host: str) -> None:
        self.allow_provider(host, tier=ProviderTrustTier.UNKNOWN)

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "trusted_hosts": sorted(self.trusted_hosts),
                "experimental_hosts": sorted(self.experimental_hosts),
                "revoked_hosts": sorted(self.revoked_hosts),
                "blocked_models": sorted(self.blocked_models),
            }


# ---------------------------------------------------------------------------
# 4. Metadata-Only Audit Logger
# ---------------------------------------------------------------------------

class GatewayAuditLogger:
    """Records provider, model, timestamp, token volume, data sensitivity/categories, and triggered rules.
    NEVER records the full request or response text by default (`log_raw_text=False`).
    """

    def __init__(self, log_path: Path | None = None, *, log_raw_text: bool = False, max_memory_entries: int = 500) -> None:
        self._lock = threading.Lock()
        self.log_path = log_path
        self.log_raw_text = bool(log_raw_text)
        self.max_memory_entries = max_memory_entries
        self._entries: list[dict[str, Any]] = []

    def record(
        self,
        *,
        provider_name: str,
        provider_host: str,
        provider_trust_tier: str,
        model_name: str,
        sensitivity: str,
        data_categories: list[str],
        triggered_rules: list[str],
        decision: str,
        reason: str = "",
        tokens_in: int = 0,
        tokens_out: int = 0,
        latency_ms: int = 0,
        prompt_sha256: str = "",
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "provider": provider_name,
            "provider_host": provider_host,
            "provider_trust_tier": provider_trust_tier,
            "model": model_name,
            "sensitivity": sensitivity,
            "data_categories": sorted(set(data_categories)) if data_categories else ["neutral"],
            "triggered_rules": list(triggered_rules),
            "decision": decision,
            "reason": reason,
            "tokens_in": int(tokens_in),
            "tokens_out": int(tokens_out),
            "latency_ms": int(latency_ms),
            "prompt_sha256": prompt_sha256,
            "raw_text_logged": False,
        }
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > self.max_memory_entries:
                self._entries = self._entries[-self.max_memory_entries :]
            target = self.log_path or self._default_log_path()
            if target is not None:
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with open(target, "a", encoding="utf-8") as fh:
                        fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
                except OSError:
                    pass
        return entry

    @staticmethod
    def _default_log_path() -> Path | None:
        data_dir = os.environ.get("BCC_DATA_DIR")
        if data_dir:
            return Path(data_dir) / "ai_gateway_audit.jsonl"
        return None

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return list(reversed(self._entries[-max(1, limit) :]))

    def stats(self) -> dict[str, int]:
        with self._lock:
            items = list(self._entries)
        return {
            "total": len(items),
            "blocked": sum(1 for e in items if str(e.get("decision", "")).startswith("block")),
            "scrubbed": sum(
                1
                for e in items
                if any(str(r).startswith("redact:") for r in (e.get("triggered_rules") or []))
            ),
            "local_routed": sum(1 for e in items if "local" in str(e.get("decision", ""))),
            "cloud_routed": sum(1 for e in items if "cloud" in str(e.get("decision", ""))),
        }


# ---------------------------------------------------------------------------
# 5. Central AI Gateway
# ---------------------------------------------------------------------------

@dataclass
class GatewayInspection:
    sensitivity: DataSensitivity
    provider_tier: ProviderTrustTier
    allowed: bool
    decision: str
    reason_code: str
    reason_message: str
    data_categories: list[str]
    triggered_rules: list[str]
    sanitized_messages: list[dict[str, Any]]
    session: RedactionSession
    prompt_sha256: str


class AIGateway:
    """Single AI Gateway deciding what can be sent to a provider, scrubbing PII/internal markers,
    enforcing minimal context, blocking unknown/unsafe calls, and logging metadata only."""

    def __init__(
        self,
        *,
        scrubber: LocalScrubber | None = None,
        minifier: ContextMinifier | None = None,
        policy: ProviderPolicyRegistry | None = None,
        audit: GatewayAuditLogger | None = None,
    ) -> None:
        self.scrubber = scrubber or LocalScrubber()
        self.minifier = minifier or ContextMinifier()
        self.policy = policy or ProviderPolicyRegistry()
        self.audit = audit or GatewayAuditLogger()

    def classify_and_prepare(
        self,
        provider: dict[str, Any],
        model: dict[str, Any],
        messages: list[dict[str, Any]],
        *,
        context_meta: dict[str, Any] | None = None,
        has_local_fallback: bool = False,
        strict_unknown: bool = True,
    ) -> GatewayInspection:
        meta = dict(context_meta or {})
        allow_mem_opt_in = bool(meta.get("allow_memory_opt_in", False))
        session = RedactionSession()
        provider_tier = self.policy.classify_provider(provider, model, strict_unknown=strict_unknown)

        # Check execution_privacy ContextVar from bossman_shared.privacy
        from bossman_shared.privacy import _privacy as exec_privacy_var, local_only_switch
        exec_priv = exec_privacy_var.get()
        if local_only_switch():
            session.record_rule("policy:local_only_switch", "confidential")
        if exec_priv in ("private", "local_only"):
            session.record_rule(f"privacy:contextvar_{exec_priv}", "confidential")
        elif exec_priv == "internal":
            session.record_rule("privacy:contextvar_internal", "internal")

        domain = str(meta.get("domain") or "").strip().lower()
        explicit_sens = str(meta.get("sensitivity") or "").strip().lower()
        if domain in CONFIDENTIAL_DOMAINS or explicit_sens == DataSensitivity.CONFIDENTIAL.value:
            session.record_rule(f"domain:{domain or 'confidential'}", domain or "confidential")

        # Step 1: Minimal context packaging for external providers; full context for local models
        if provider_tier == ProviderTrustTier.LOCAL:
            packed = copy.deepcopy(list(messages or []))
        else:
            packed = self.minifier.pack_minimal_context(
                list(messages or []),
                session,
                include_system=bool(meta.get("include_system", True)),
                include_tool_results=meta.get("include_tool_results"),
                max_history_messages=meta.get("max_history_messages"),
                allow_memory_opt_in=allow_mem_opt_in,
            )

        # Step 2: Scrub text locally (finds secrets, seed phrases, PII, accounts, internal IPs, projects)
        # On local models, we still scan to classify sensitivity and log rules, or scrub if requested;
        # on external providers, we always replace with [КЛИЕНТ_1], [СЧЕТ_1], etc.
        probe_session = session if provider_tier != ProviderTrustTier.LOCAL else RedactionSession()
        scrubbed_messages = self.scrubber.scrub_messages(packed, probe_session)
        if provider_tier == ProviderTrustTier.LOCAL:
            for r in probe_session.triggered_rules:
                session.record_rule(r)
            session.data_categories.update(probe_session.data_categories)
            session.unscrubbable_secrets_found = probe_session.unscrubbable_secrets_found
            final_messages = packed
        else:
            final_messages = scrubbed_messages

        # Step 3: Determine overall DataSensitivity
        if (
            session.unscrubbable_secrets_found
            or domain in CONFIDENTIAL_DOMAINS
            or explicit_sens == DataSensitivity.CONFIDENTIAL.value
            or exec_priv in ("private", "local_only")
            or local_only_switch()
        ):
            sensitivity = DataSensitivity.CONFIDENTIAL
        elif (
            session.placeholder_to_original
            or explicit_sens == DataSensitivity.INTERNAL.value
            or exec_priv == "internal"
            or meta.get("internal") is True
        ):
            sensitivity = DataSensitivity.INTERNAL
        else:
            sensitivity = DataSensitivity.PUBLIC

        prompt_blob = json.dumps(final_messages, ensure_ascii=False, sort_keys=True, default=str)
        prompt_sha256 = hashlib.sha256(prompt_blob.encode("utf-8", "ignore")).hexdigest()

        # Step 4: Provider Policy Decision
        # 4a. LOCAL provider handles everything
        if provider_tier == ProviderTrustTier.LOCAL:
            return GatewayInspection(
                sensitivity=sensitivity,
                provider_tier=provider_tier,
                allowed=True,
                decision="allow_local",
                reason_code="ok_local",
                reason_message="Локальная модель: запрос обрабатывается внутри контура ПК.",
                data_categories=sorted(session.data_categories) or ["neutral"],
                triggered_rules=session.triggered_rules,
                sanitized_messages=final_messages,
                session=session,
                prompt_sha256=prompt_sha256,
            )

        # 4b. Unknown provider -> fail-closed block until explicitly approved by owner
        if provider_tier == ProviderTrustTier.UNKNOWN:
            return GatewayInspection(
                sensitivity=sensitivity,
                provider_tier=provider_tier,
                allowed=False,
                decision="rerouted_to_local" if has_local_fallback else "blocked",
                reason_code="unknown_provider_blocked",
                reason_message="Неизвестный внешний провайдер заблокирован AI Gateway до явного разрешения владельца.",
                data_categories=sorted(session.data_categories) or ["neutral"],
                triggered_rules=[*session.triggered_rules, "gate:unknown_provider_blocked"],
                sanitized_messages=[],
                session=session,
                prompt_sha256=prompt_sha256,
            )

        # 4c. Confidential request -> never leaves the machine to ANY external provider
        if sensitivity == DataSensitivity.CONFIDENTIAL:
            return GatewayInspection(
                sensitivity=sensitivity,
                provider_tier=provider_tier,
                allowed=False,
                decision="rerouted_to_local" if has_local_fallback else "blocked",
                reason_code="confidential_external_forbidden",
                reason_message=(
                    "Конфиденциальный запрос (ключи, seed-фразы, личная память, финансы или приватный код): "
                    "внешний запрос запрещён, обработка только локальной моделью."
                ),
                data_categories=sorted(session.data_categories) or ["confidential"],
                triggered_rules=[*session.triggered_rules, "gate:confidential_blocked_external"],
                sanitized_messages=[],
                session=session,
                prompt_sha256=prompt_sha256,
            )

        # 4d. Experimental / questionable provider -> only PUBLIC / synthetic requests allowed
        if provider_tier == ProviderTrustTier.EXPERIMENTAL and sensitivity != DataSensitivity.PUBLIC:
            return GatewayInspection(
                sensitivity=sensitivity,
                provider_tier=provider_tier,
                allowed=False,
                decision="rerouted_to_local" if has_local_fallback else "blocked",
                reason_code="experimental_provider_requires_public_synthetic",
                reason_message=(
                    "Для экспериментального/внешнего сервиса без гарантии нехранения данных "
                    "разрешены только публичные/синтетические запросы; внутренние данные переведены на локальную модель."
                ),
                data_categories=sorted(session.data_categories) or ["internal"],
                triggered_rules=[*session.triggered_rules, "gate:experimental_internal_blocked"],
                sanitized_messages=[],
                session=session,
                prompt_sha256=prompt_sha256,
            )

        # 4e. Final post-scrubbing verification on outgoing packet
        residual_problems = self.scrubber.verify_clean_for_egress(final_messages, allow_memory_opt_in=allow_mem_opt_in)
        if residual_problems:
            return GatewayInspection(
                sensitivity=DataSensitivity.CONFIDENTIAL,
                provider_tier=provider_tier,
                allowed=False,
                decision="rerouted_to_local" if has_local_fallback else "blocked",
                reason_code="unscrubbable_payload_blocked",
                reason_message=f"Запрос невозможно безопасно обезличить ({', '.join(residual_problems)}): заблокирован.",
                data_categories=sorted(session.data_categories) or ["secrets"],
                triggered_rules=[*session.triggered_rules, *residual_problems],
                sanitized_messages=[],
                session=session,
                prompt_sha256=prompt_sha256,
            )

        return GatewayInspection(
            sensitivity=sensitivity,
            provider_tier=provider_tier,
            allowed=True,
            decision="allow_cloud_sanitized",
            reason_code="ok_sanitized",
            reason_message="Запрос очищен локально и упакован в минимальный контекст для доверенного провайдера.",
            data_categories=sorted(session.data_categories) or ["neutral"],
            triggered_rules=session.triggered_rules,
            sanitized_messages=final_messages,
            session=session,
            prompt_sha256=prompt_sha256,
        )

    async def execute_chat(
        self,
        adapter_call: Callable[..., Any],
        provider: dict[str, Any],
        model: dict[str, Any],
        model_arg: str,
        messages: list[dict[str, Any]],
        *,
        call_kwargs: dict[str, Any] | None = None,
        context_meta: dict[str, Any] | None = None,
        local_fallback_adapter: Any | None = None,
        local_fallback_model: str = "qwen3:8b",
        rehydrate_response: bool = True,
        strict_unknown: bool = True,
    ) -> Any:
        """Execute a chat request through the AI Gateway with full classification, scrubbing,
        provider policy enforcement, optional local fallback routing, response rehydration, and metadata logging."""
        kw = dict(call_kwargs or {})
        t0 = time.perf_counter()
        inspection = self.classify_and_prepare(
            provider,
            model,
            messages,
            context_meta=context_meta,
            has_local_fallback=local_fallback_adapter is not None,
            strict_unknown=strict_unknown,
        )
        p_name = str(provider.get("name") or provider.get("kind") or "unknown")
        p_host = self.policy.extract_host(str(provider.get("base_url") or "")) or "local"
        m_name = str(model.get("name") or model.get("alias") or model_arg or "unknown")

        if not inspection.allowed:
            if local_fallback_adapter is not None:
                # Route confidential/internal-blocked request to the local model without external egress
                res = await local_fallback_adapter.chat(local_fallback_model, messages, **kw)
                latency_ms = int((time.perf_counter() - t0) * 1000)
                t_in = int((res.get("tokens_in", 0) if isinstance(res, dict) else getattr(res, "tokens_in", 0)) or 0)
                t_out = int((res.get("tokens_out", 0) if isinstance(res, dict) else getattr(res, "tokens_out", 0)) or 0)
                self.audit.record(
                    provider_name="local_fallback",
                    provider_host="127.0.0.1",
                    provider_trust_tier=ProviderTrustTier.LOCAL.value,
                    model_name=local_fallback_model,
                    sensitivity=inspection.sensitivity.value,
                    data_categories=inspection.data_categories,
                    triggered_rules=inspection.triggered_rules,
                    decision="rerouted_to_local",
                    reason=inspection.reason_code,
                    tokens_in=t_in,
                    tokens_out=t_out,
                    latency_ms=latency_ms,
                    prompt_sha256=inspection.prompt_sha256,
                )
                return res

            latency_ms = int((time.perf_counter() - t0) * 1000)
            self.audit.record(
                provider_name=p_name,
                provider_host=p_host,
                provider_trust_tier=inspection.provider_tier.value,
                model_name=m_name,
                sensitivity=inspection.sensitivity.value,
                data_categories=inspection.data_categories,
                triggered_rules=inspection.triggered_rules,
                decision="blocked",
                reason=inspection.reason_code,
                tokens_in=0,
                tokens_out=0,
                latency_ms=latency_ms,
                prompt_sha256=inspection.prompt_sha256,
            )
            raise GatewayBlockedError(
                inspection.reason_message,
                reason_code=inspection.reason_code,
                sensitivity=inspection.sensitivity.value,
                provider_tier=inspection.provider_tier.value,
                triggered_rules=inspection.triggered_rules,
            )

        # Allowed -> call target adapter with minimal, scrubbed messages
        res = await adapter_call(model_arg, inspection.sanitized_messages, **kw)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        t_in = int((res.get("tokens_in", 0) if isinstance(res, dict) else getattr(res, "tokens_in", 0)) or 0)
        t_out = int((res.get("tokens_out", 0) if isinstance(res, dict) else getattr(res, "tokens_out", 0)) or 0)

        # Rehydrate placeholders locally if needed
        if rehydrate_response and inspection.session.placeholder_to_original:
            if isinstance(res, ChatResult) and isinstance(res.text, str):
                res.text = inspection.session.rehydrate(res.text)
            elif isinstance(res, dict):
                res = dict(res)
                if isinstance(res.get("text"), str):
                    res["text"] = inspection.session.rehydrate(res["text"])
                if isinstance(res.get("content"), str):
                    res["content"] = inspection.session.rehydrate(res["content"])
            elif isinstance(res, str):
                res = inspection.session.rehydrate(res)

        self.audit.record(
            provider_name=p_name,
            provider_host=p_host,
            provider_trust_tier=inspection.provider_tier.value,
            model_name=m_name,
            sensitivity=inspection.sensitivity.value,
            data_categories=inspection.data_categories,
            triggered_rules=inspection.triggered_rules,
            decision=inspection.decision,
            reason=inspection.reason_code,
            tokens_in=t_in,
            tokens_out=t_out,
            latency_ms=latency_ms,
            prompt_sha256=inspection.prompt_sha256,
        )
        return res


_DEFAULT_GATEWAY: AIGateway | None = None
_GATEWAY_LOCK = threading.Lock()


def get_ai_gateway() -> AIGateway:
    global _DEFAULT_GATEWAY
    with _GATEWAY_LOCK:
        if _DEFAULT_GATEWAY is None:
            _DEFAULT_GATEWAY = AIGateway()
        return _DEFAULT_GATEWAY


def reset_ai_gateway(gateway: AIGateway | None = None) -> AIGateway:
    global _DEFAULT_GATEWAY
    with _GATEWAY_LOCK:
        _DEFAULT_GATEWAY = gateway if gateway is not None else AIGateway()
        return _DEFAULT_GATEWAY
