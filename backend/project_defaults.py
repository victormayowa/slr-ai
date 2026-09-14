import models

DEFAULT_EXTRACTION_FIELDS = ["Sample Size", "Mean Age", "Primary Outcome Result", "Adverse Events"]


def apply_project_defaults(project: models.Project) -> None:
    """Give a new project its protocol record and starting extraction fields."""
    if project.protocol is None:
        project.protocol = models.Protocol()
    if not project.extraction_fields:
        project.extraction_fields = [
            models.ExtractionField(name=name, position=position)
            for position, name in enumerate(DEFAULT_EXTRACTION_FIELDS)
        ]
