from sqlalchemy import Column, Integer, String, Float, ForeignKey, UniqueConstraint
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Researcher(Base):
    __tablename__ = "researcher"

    researcher_id = Column(Integer, primary_key=True)

    name = Column(String, nullable=False)
    job_title = Column(String)
    academic_level = Column(String)
    university = Column(String, nullable=False)
    field_of_research = Column(String)
    source_id = Column(String)
    orcid = Column(String)
    profile_url = Column(String)

    publications = relationship(
        "Publication",
        back_populates="researcher",
        cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("university", "name", name="uq_researcher_university_name"),
    )


class Journal(Base):
    __tablename__ = "journal"

    journal_id = Column(Integer, primary_key=True)

    journal_name = Column(String, nullable=False, unique=True)
    journal_raw = Column(String)
    publisher = Column(String)
    issn = Column(String)

    quality_rank = Column(String)
    abdc_edition = Column(String)

    impact_factor = Column(Float)
    impact_factor_5yr = Column(Float)
    jcr_year = Column(Integer)

    sjr = Column(Float)
    sjr_quartile = Column(String)
    h_index = Column(Integer)
    cites_per_doc_2y = Column(Float)
    scimago_year = Column(String)

    publications = relationship(
        "Publication",
        back_populates="journal"
    )


class Publication(Base):
    __tablename__ = "publication"

    publication_id = Column(Integer, primary_key=True)

    researcher_id = Column(
        Integer,
        ForeignKey("researcher.researcher_id"),
        nullable=False
    )

    journal_id = Column(
        Integer,
        ForeignKey("journal.journal_id")
    )

    title = Column(String, nullable=False)
    year = Column(Integer)
    author_count = Column(Integer)
    authors = Column(String)

    doi = Column(String)
    article_url = Column(String)
    link = Column(String)

    quality_rank = Column(String)
    sjr_quartile = Column(String)

    citation_percentile = Column(Float)
    cited_by_count = Column(Integer)
    fwci = Column(Float)

    oa_status = Column(String)
    oa_url = Column(String)

    publication_status = Column(String)
    source = Column(String)

    researcher = relationship(
        "Researcher",
        back_populates="publications"
    )

    journal = relationship(
        "Journal",
        back_populates="publications"
    )


class Harvest(Base):
    __tablename__ = "harvest"

    harvest_id = Column(Integer, primary_key=True)

    source = Column(String, nullable=False)
    last_run = Column(String)
    latest_year = Column(Integer)