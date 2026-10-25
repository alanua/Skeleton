from core.device_capability_contract import DeviceActionPlan, DeviceRisk, validate_device_action


def make_plan(**updates):
    fields = dict(
        device_id="brother_printer",
        capability="print_head_clean_normal",
        node_id="home-edge-01",
        execution_lane=DeviceRisk.ROUTINE_MUTATION,
        run_as="desktop-user",
        timeout_seconds=90,
        request_id="brother-clean-test-request",
        idempotency_key="brother-clean-test-once",
        argv=("/usr/local/bin/approved-brother-adapter", "clean-normal"),
        operator_approval_ref="approval-receipt-from-canonical-gateway",
        requires_secret=True,
        secret_ref="bitwarden-sm:opaque-reference",
        verification="nozzle check with external physical evidence",
    )
    fields.update(updates)
    return DeviceActionPlan(**fields)


def test_approved_exactly_scoped_plan_is_valid():
    result = validate_device_action(
        make_plan(), approved_scope="brother_printer:print_head_clean_normal"
    )
    assert result.permitted
    assert result.reasons == ()


def test_mutation_without_operator_consent_fails_closed():
    result = validate_device_action(
        make_plan(operator_approval_ref=None), approved_scope=None
    )
    assert not result.permitted
    assert any("consent" in reason for reason in result.reasons)


def test_scope_mismatch_rejected():
    result = validate_device_action(
        make_plan(), approved_scope="other_device:print_head_clean_normal"
    )
    assert not result.permitted


def test_second_executor_or_missing_verification_rejected():
    result = validate_device_action(
        make_plan(node_id="other-node", verification=""),
        approved_scope="brother_printer:print_head_clean_normal",
    )
    assert not result.permitted
    assert len(result.reasons) >= 2


def test_plaintext_secret_in_argv_rejected():
    result = validate_device_action(
        make_plan(argv=("clean", "password=plaintext")),
        approved_scope="brother_printer:print_head_clean_normal",
    )
    assert not result.permitted


def test_secret_reference_required():
    result = validate_device_action(
        make_plan(secret_ref=None),
        approved_scope="brother_printer:print_head_clean_normal",
    )
    assert not result.permitted


def test_destructive_action_without_rollback_rejected():
    result = validate_device_action(
        make_plan(execution_lane=DeviceRisk.DESTRUCTIVE, rollback=""),
        approved_scope="brother_printer:print_head_clean_normal",
    )
    assert not result.permitted


def test_read_only_action_does_not_require_approval():
    result = validate_device_action(
        make_plan(
            execution_lane=DeviceRisk.READ_ONLY,
            operator_approval_ref=None,
            requires_secret=False,
            secret_ref=None,
        ),
        approved_scope=None,
    )
    assert result.permitted
