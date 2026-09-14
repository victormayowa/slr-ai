from sqlalchemy.orm import Session

import models
from ai_catalog import default_model

DEFAULT_EXTRACTION_FIELDS = ["Sample Size", "Mean Age", "Primary Outcome Result", "Adverse Events"]


def apply_project_defaults(db: Session, project: models.Project) -> None:
    """Give a new project its protocol record, starting extraction fields, and the catalog's default models."""
    if project.protocol is None:
        project.protocol = models.Protocol()
    if not project.extraction_fields:
        project.extraction_fields = [
            models.ExtractionField(name=name, position=position)
            for position, name in enumerate(DEFAULT_EXTRACTION_FIELDS)
        ]
    if project.ai_model is None:
        project.ai_model = default_model(db)
    if project.embedding_model is None:
        project.embedding_model = default_model(db, "embedding")
