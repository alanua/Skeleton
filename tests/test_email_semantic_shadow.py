from core.email_semantic_shadow import aggregate_threads, normalize_message_text, prototype_predict


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
