from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey
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
    publications = relationship("Publication", back_populates="researcher")

class Journal(Base):
    __tablename__ = "journal"
    journal_id        = Column(Integer, primary_key=True)
    journal_name      = Column(String, nullable=False, unique=True)
    issn              = Column(String)
    quality_rank      = Column(String)     # A*, A, B, C
    impact_factor     = Column(Float)
    impact_factor_5yr = Column(Float)
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
