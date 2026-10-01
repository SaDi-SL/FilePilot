from dataclasses import dataclass

from app.application_service import ActivityStatus
from app.ui.qt.theme.tokens import COLORS


@dataclass(frozen=True)
class ActivityStatusPresentation:
    label: str
    tone: str
    color: str


_PRESENTATIONS = {
    ActivityStatus.COMPLETED: ActivityStatusPresentation(
        "Completed", "success", COLORS.success
    ),
    ActivityStatus.DUPLICATE: ActivityStatusPresentation(
        "Duplicate", "warning", COLORS.warning
    ),
    ActivityStatus.FAILED: ActivityStatusPresentation(
        "Failed", "error", COLORS.error
    ),
    ActivityStatus.NEEDS_REVIEW: ActivityStatusPresentation(
        "Needs review", "error", COLORS.error
    ),
    ActivityStatus.IN_PROGRESS: ActivityStatusPresentation(
        "In progress", "info", COLORS.info
    ),
    ActivityStatus.WARNING: ActivityStatusPresentation(
        "Warning", "warning", COLORS.warning
    ),
    ActivityStatus.RESTORED: ActivityStatusPresentation(
        "Restored", "info", COLORS.info
    ),
    ActivityStatus.UNKNOWN: ActivityStatusPresentation(
        "Unknown", "neutral", COLORS.text_muted
    ),
}


def activity_status_presentation(
    status: ActivityStatus,
) -> ActivityStatusPresentation:
    return _PRESENTATIONS.get(status, _PRESENTATIONS[ActivityStatus.UNKNOWN])
