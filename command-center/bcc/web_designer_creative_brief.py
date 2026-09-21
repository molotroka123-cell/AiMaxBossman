"""Canonical creative brief for AI-assisted website creation in Bossman Web Designer.

This is deliberately kept separate from the deterministic fallback generator.
The fallback must remain reproducible for CI/offline use. When an AI model is
available, the site-build orchestration should use this brief as the default
creative/engineering contract, then verify the result with the normal Web
Designer safety, preview, versioning and UX gates.
"""

CREATIVE_BRIEF_VERSION = "premium-studio-v1"

STAGES: tuple[dict[str, str], ...] = (
    {
        "id": "creative_direction",
        "title": "DEFINE THE LUXURY CREATIVE DIRECTION",
        "prompt": (
            "Act as a world-class digital art director for a boutique luxury agency. "
            "Analyze the brand, audience, offer and supplied references. Define a coherent "
            "creative direction covering visual identity, color system, typography, materials, "
            "lighting, composition, imagery, spatial language and emotional tone. Every decision "
            "must feel intentional, distinctive and premium rather than decorative."
        ),
    },
    {
        "id": "visual_world",
        "title": "DESIGN THE ULTRA-PREMIUM VISUAL WORLD",
        "prompt": (
            "Act as an elite 3D/visual experience designer. Define the hero environment, visual "
            "objects, materials, textures, lighting, reflections, depth, camera perspective, "
            "atmosphere and transitions between sections. Use 3D only when it improves the brand "
            "experience; it must feel integrated rather than ornamental and must degrade gracefully "
            "on constrained devices."
        ),
    },
    {
        "id": "experience_architecture",
        "title": "ARCHITECT THE PREMIUM EXPERIENCE",
        "prompt": (
            "Act as a senior creative UX director. Architect the complete journey: navigation, "
            "hero, storytelling, product/service presentation, social proof, calls to action, "
            "transitions and final conversion path. Give every section a clear purpose and build "
            "a deliberate visual hierarchy and narrative progression."
        ),
    },
    {
        "id": "cinematic_motion",
        "title": "ENGINEER CINEMATIC ANIMATION",
        "prompt": (
            "Act as an award-winning motion designer. Define purposeful scroll-driven movement, "
            "camera transitions, object motion, depth/parallax, reveals, scale, masking, lighting "
            "shifts and premium easing. Motion must be controlled, performant and optional: honor "
            "prefers-reduced-motion and never sacrifice usability for spectacle."
        ),
    },
    {
        "id": "micro_interactions",
        "title": "CREATE HIGH-END MICRO-INTERACTIONS",
        "prompt": (
            "Act as a senior interaction designer. Design hover/focus states, cursor behavior where "
            "appropriate, magnetic interactions only when usable, navigation transitions, image/3D "
            "responses, loading experience, scroll feedback and section transitions. Every "
            "interaction must communicate quality while remaining intuitive, keyboard-accessible, "
            "responsive and fast."
        ),
    },
    {
        "id": "production_engineering",
        "title": "CODE IT LIKE A BOUTIQUE AGENCY",
        "prompt": (
            "Act as a senior creative developer. Turn the approved concept into production-ready "
            "code using the project's existing stack and conventions. Inspect existing code first "
            "and preserve working functionality. Use clean architecture, reusable components, "
            "responsive behavior, accessible interactions, optimized assets and maintainable motion. "
            "Prioritize visual fidelity AND performance; do not add a framework merely for prestige."
        ),
    },
    {
        "id": "studio_polish",
        "title": "GIVE IT THE FINAL STUDIO POLISH",
        "prompt": (
            "Act as the creative director performing final agency review. Audit typography, spacing, "
            "composition, lighting, depth, animation timing, transitions, interactions, responsiveness, "
            "accessibility, performance and conversion clarity. Identify the highest-impact "
            "imperfections, implement justified refinements, and verify the final experience feels "
            "cohesive, sophisticated and genuinely production-ready."
        ),
    },
)

QUALITY_GATES: tuple[str, ...] = (
    "mobile_and_desktop_responsive",
    "keyboard_accessible",
    "prefers_reduced_motion",
    "no_unexplained_console_errors",
    "no_dead_controls",
    "no_false_success_or_fake_conversion",
    "reasonable_performance_budget",
    "preview_sandbox_preserved",
    "version_and_rollback_preserved",
    "owner_preview_before_publish",
)


def render_default_creative_brief(*, brand: str, audience: str = "",
                                  offer: str = "", references: str = "") -> str:
    """Render the canonical AI build brief with owner/project context."""

    context = (
        f"BRAND: {brand or 'infer from the project brief'}\n"
        f"AUDIENCE: {audience or 'infer carefully; state assumptions'}\n"
        f"OFFER: {offer or 'infer carefully; state assumptions'}\n"
        f"REFERENCES: {references or 'none supplied'}\n"
    )
    stages = "\n\n".join(
        f"{index}. {stage['title']}\n{stage['prompt']}"
        for index, stage in enumerate(STAGES, start=1)
    )
    gates = "\n".join(f"- {gate}" for gate in QUALITY_GATES)
    return (
        "BOSSMAN WEB DESIGNER — PREMIUM STUDIO BASELINE\n\n"
        + context
        + "\nExecute the following stages as one coherent design/engineering process. "
          "Do not blindly maximize luxury, 3D or animation: fit the brand and task.\n\n"
        + stages
        + "\n\nFINAL QUALITY GATES\n"
        + gates
    )
