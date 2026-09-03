from core.email_semantic_shadow import aggregate_threads, html_to_text, normalize_message_text, normalize_subject, prototype_predict


def test_normalize_removes_quoted_reply_and_signature():
    text = "New answer\n-- \nSignature\nOn Tue, Someone wrote:\n> old text"
    assert normalize_message_text(text) == "New answer"


def test_aggregate_threads_groups_and_preserves_order():
    rows = [
        {"message_id":"m2","thread_id":"t","internal_ms":2,"subject":"Re: X","from_addr":"b","to_addr":"a","body_text":"second","label_ids":"INBOX,CATEGORY_UPDATES"},
        {"message_id":"m1","thread_id":"t","internal_ms":1,"subject":"X","from_addr":"a","to_addr":"b","body_text":"first","label_ids":"SENT"},
    ]
    [thread] = aggregate_threads(rows)
    assert thread.message_ids == ("m1", "m2")
    assert thread.subject == "X"
    assert "first" in thread.digest and "second" in thread.digest
    assert set(thread.native_labels) == {"INBOX", "CATEGORY_UPDATES", "SENT"}


def test_prototype_prediction_requires_margin_for_high():
    prototypes = {"ads":[[1.0,0.0]], "finance":[[0.6,0.8]]}
    pred = prototype_predict([1.0,0.0], prototypes, high_similarity=.9, high_margin=.1)
    assert pred.label == "ads"
    assert pred.confidence_tier == "HIGH"


def test_prototype_prediction_mid_when_close_boundary():
    prototypes = {"ads":[[1.0,0.0]], "finance":[[0.99,0.01]]}
    pred = prototype_predict([1.0,0.0], prototypes, high_similarity=.9, high_margin=.1, mid_similarity=.7)
    assert pred.confidence_tier == "MID"


def test_normalize_outlook_header_block():
    text = "Current answer\n\nFrom: Alice <a@example.com>\nSent: Tuesday\nTo: Bob <b@example.com>\nSubject: Old\nOld body"
    assert normalize_message_text(text) == "Current answer"


def test_subject_prefixes_are_removed_repeatedly():
    assert normalize_subject("Re: AW: Fwd: Project X") == "Project X"


def test_html_fallback_is_readable():
    assert "Hello world" in html_to_text("<p>Hello <b>world</b></p>")


def test_thread_digest_is_capped_and_keeps_latest_message():
    rows = [
        {"message_id":f"m{i}","thread_id":"t","internal_ms":i,"subject":"Re: Topic","from_addr":"a","to_addr":"b","body_text":("message-%d " % i) + ("x"*700)}
        for i in range(6)
    ]
    [thread] = aggregate_threads(rows, max_chars_per_message=800, max_chars_per_thread=1500)
    assert len(thread.digest) <= 1500
    assert thread.digest_truncated is True
    assert thread.message_count == 6
    assert thread.subject == "Topic"
    assert "message-5" in thread.digest or len(thread.digest) == 1500
