from sqlalchemy import Column, Integer, String, Float, ForeignKey
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class Researcher(Base):
    __tablename__ = "researcher"
    researcher_id     = Column(Integer, primary_key=True)
    name              = Column(String, nullable=False)
    job_title         = Column(String)
    academic_level    = Column(String)     # A–E, nullable
    field_of_research = Column(String)     # Accounting | Finance
    profile_url       = Column(String)
    university        = Column(String, nullable=False)
    orcid             = Column(String)               # nullable
    publications = relationship("Publication", back_populates="researcher")

class Journal(Base):
    __tablename__ = "journal"
    journal_id        = Column(Integer, primary_key=True)
    journal_name      = Column(String, nullable=False, unique=True)
    issn              = Column(String)
    quality_rank      = Column(String)     # A*, A, B, C; nullable where unmatched
    abdc_edition      = Column(String)     # which JQL edition supplied quality_rank
    impact_factor     = Column(Float)      # nullable; excluded from averages when absent
    jcr_year          = Column(Integer)    # which JCR year supplied the impact factor
    sjr               = Column(Float)      # Scimago Journal Rank; nullable
    sjr_quartile      = Column(String)     # Q1–Q4; nullable
    h_index           = Column(Integer)    # journal h-index; nullable
    cites_per_doc_2y  = Column(Float)      # Scimago JIF analogue; nullable
    scimago_year      = Column(String)     # which Scimago edition supplied the above
    publications = relationship("Publication", back_populates="journal")

class Publication(Base):
    __tablename__ = "publication"
    publication_id      = Column(Integer, primary_key=True)
    researcher_id       = Column(Integer, ForeignKey("researcher.researcher_id"), nullable=False)
    journal_id          = Column(Integer, ForeignKey("journal.journal_id"))
    title               = Column(String, nullable=False)
    doi                 = Column(String)
    author_count        = Column(Integer)
    year                = Column(Integer)
    article_url         = Column(String)
    source              = Column(String)
    citation_percentile = Column(Float)
    researcher = relationship("Researcher", back_populates="publications")
    journal    = relationship("Journal", back_populates="publications")

class Harvest(Base):
    __tablename__ = "harvest"
    harvest_id  = Column(Integer, primary_key=True)
    source      = Column(String, nullable=False)
    last_run    = Column(String)
    latest_year = Column(Integer)
