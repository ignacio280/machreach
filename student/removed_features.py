"""Central policy for student features removed from the live product."""

DEPRECATED_STUDENT_PATHS = {
    "/student/plan",
    "/student/notes",
    "/student/chat",
    "/student/panic",
    "/student/practice",
    "/student/schedule",
    "/student/marketplace",
}

REMOVED_API_PREFIXES = {
    "/api/student/practice": "Practice has been removed",
    "/api/student/marketplace": "Marketplace has been removed",
}
