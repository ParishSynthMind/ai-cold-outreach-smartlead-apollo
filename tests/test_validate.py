from outreach.validate import validate_draft

from .conftest import good_draft


def test_good_draft_is_clean(lead, settings):
    c = validate_draft(good_draft(), lead, settings)
    assert c.clean, (c.errors, c.warnings)


def test_footer_written_by_model_is_an_error(lead, settings):
    d = good_draft(body_2=good_draft()["body_2"] + "\n\n" + settings.unsubscribe_line)
    assert any("footer" in e for e in validate_draft(d, lead, settings).errors)


def test_dash_link_number_and_placeholder(lead, settings):
    d = good_draft(body_1=good_draft()["body_1"].replace("so nobody", "— see www.spam.io, 300% faster {{x}}"))
    errs = " ".join(validate_draft(d, lead, settings).errors)
    assert "dash" in errs and "link" in errs and "300" in errs and "placeholder" in errs


def test_invented_fact_and_unknown_field(lead, settings):
    d = good_draft(facts_used=[{"field": "notes", "value": "Manchester show"}, {"field": "email", "value": "x"}])
    errs = validate_draft(d, lead, settings).errors
    assert any("not in the lead's notes" in e for e in errs)
    assert any("unknown field" in e for e in errs)


def test_unknown_name_and_unsure_are_warnings(lead, settings):
    d = good_draft(body_2="Hi Jo,\n\nJust checking in after Bristol, happy to keep it short and simple.\n\nSam",
                   unsure=True, unsure_reason="thin row")
    c = validate_draft(d, lead, settings)
    assert c.ok and not c.clean
    assert any("Bristol" in w for w in c.warnings) and any("unsure" in w for w in c.warnings)


def test_word_limits_and_subject(lead, settings):
    d = good_draft(subject_1="x" * 61, body_2="Hi Jo.")
    errs = validate_draft(d, lead, settings).errors
    assert any("subject" in e for e in errs) and any("body_2 is" in e for e in errs)


def test_missing_fields():
    from outreach.leads import Lead

    from .conftest import make_settings
    c = validate_draft({"subject_1": "", "body_1": "x"}, Lead("a@b.co", "A", "B", "c"), make_settings())
    assert len(c.errors) == 2
