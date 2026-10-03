from admin import ENTITIES


def test_publication_export_uses_the_agreed_researcher_field_name():
    columns = [column for column, *_ in ENTITIES["publications"]["readonly"]]
    assert "name" in columns
    assert "researcher_name" not in columns


def test_all_client_required_field_names_exist_in_exports():
    fields = {}
    for entity, config in ENTITIES.items():
        fields[entity] = {config["key"], *config["editable"]}
        fields[entity].update(column for column, *_ in config.get("readonly", []))

    assert {"name", "job_title", "academic_level", "field_of_research",
            "university", "profile_url"} <= fields["researchers"]
    assert {"name", "title", "year", "doi", "article_url",
            "author_count", "source"} <= fields["publications"]
    assert {"journal_name", "issn", "quality_rank",
            "impact_factor"} <= fields["journals"]
