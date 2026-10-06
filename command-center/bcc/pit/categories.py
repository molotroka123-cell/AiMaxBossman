from __future__ import annotations

# Collection-first taxonomy. These are memory candidate buckets, not traits the
# assistant may assume. A category is populated only from the current
# participant's own conversation/evidence.
CATEGORIES: dict[str, tuple[str, ...]] = {
    "communication": (
        "primary_language", "other_languages", "answer_length", "answer_format",
        "formality", "technical_depth", "examples_preference", "source_preference",
        "initiative_preference", "clarification_preference", "explanation_style",
        "vocabulary_style", "date_format", "currency_format", "voice_text_preference",
    ),
    "knowledge": (
        "known_topics", "novice_topics", "professional_domains", "programming_languages",
        "tools_known", "learning_goals", "explained_concepts", "verified_skills",
    ),
    "work": (
        "active_projects", "project_roles", "current_tasks", "deadlines", "apps_services",
        "workflow_preferences", "document_types", "definition_of_done",
        "quality_requirements", "preferred_tools", "avoided_tools", "blockers",
        "decisions_and_reasons",
    ),
    "interests": (
        "hobbies", "sports", "technology", "games", "music", "movies", "books",
        "cars", "travel", "design", "photography", "science", "business_topics",
    ),
    "media": (
        "genres", "creators", "tone", "visual_styles", "liked_examples",
        "disliked_styles", "realism_stylization", "video_format", "image_format",
        "platforms",
    ),
    "shopping": (
        "decision_criteria", "price_quality", "premium_preference", "price_sensitivity",
        "comparison_preference", "brand_preference", "new_vs_proven", "decision_speed",
        "warranty_service", "shopping_platforms", "device_ecosystem",
    ),
    "travel": (
        "trip_style", "transport", "comfort_adventure", "pace", "activity_preferences",
        "area_preference", "lodging_type", "luggage_habits", "connection_tolerance",
    ),
    "food": (
        "cuisines", "liked_foods", "disliked_foods", "restaurant_style",
        "coffee_tea", "delivery_cooking",
    ),
    "decision": (
        "priority_criteria", "shortlist_depth", "worst_case_preference",
        "conservative_experimental", "reversibility_preference",
        "uncertainty_tolerance", "second_opinion", "numbers_examples_analogies",
    ),
    "assistant_usage": (
        "request_types", "usage_patterns", "dialog_length", "revision_frequency",
        "error_definition", "useful_answer_patterns", "tool_usage", "web_preference",
    ),
    "corrections": (
        "fact_corrections", "term_corrections", "do_not_repeat", "always_do",
        "rejected_recommendations", "successful_recommendations", "preference_changes",
    ),
    "goals": (
        "short_term", "long_term", "current_priority", "completed", "paused",
        "dependencies", "success_criteria", "confirmed_next_steps",
    ),
    "relationships": (
        "self_reported_relation_labels", "shared_projects", "communication_preferences",
    ),
    "temporal": (
        "recurring_tasks", "seasonal_projects", "interest_changes", "freshness",
    ),
    "device_software": (
        "operating_system", "devices", "ides", "browsers", "messengers",
        "file_formats", "cloud_services", "ai_tools",
    ),
    "visual_context": (
        "shared_photo_subjects", "visual_preferences", "photo_edit_preferences",
        "favorite_visual_styles", "recurrent_visible_objects", "visible_document_topics",
    ),
}

ALL_KEYS = frozenset(key for keys in CATEGORIES.values() for key in keys)
