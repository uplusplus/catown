from __future__ import annotations

from services.test_runner_contract import (
    is_valid_test_runner_result,
    normalize_test_runner_result,
)


def test_pytest_failures_are_valid_test_result():
    contract = normalize_test_runner_result(
        command="python -m pytest backend/tests -q --tb=short --disable-warnings -r fE",
        status="failed",
        success=False,
        result_text="""
...............................................................F........ [  9%]
.................................................................         [100%]
=================================== FAILURES ===================================
backend/tests/test_api_routes.py:537: in test_example
    assert approved["status"] == "approved"
E   KeyError: 'status'
=========================== short test summary info ============================
FAILED backend/tests/test_api_routes.py::test_example
5 failed, 764 passed, 1953 warnings in 305.94s
""",
    )

    assert contract["runner_status"] == "completed"
    assert contract["test_status"] == "failed"
    assert contract["counts"]["failed"] == 5
    assert contract["counts"]["passed"] == 764
    assert contract["environment_errors"] == []
    assert is_valid_test_runner_result(contract) is True


def test_pytest_collection_error_is_runner_failure():
    contract = normalize_test_runner_result(
        command="python -m pytest backend/tests -q",
        status="failed",
        success=False,
        result_text="""
ERROR collecting backend/tests/test_api_routes.py
ImportError while importing test module
ModuleNotFoundError: No module named 'fastapi'
""",
    )

    assert contract["runner_status"] == "failed"
    assert contract["test_status"] == "unknown"
    assert contract["environment_errors"][0]["kind"] in {"dependency_missing", "dependency_or_import_error", "collection_error"}
    assert is_valid_test_runner_result(contract) is False


def test_approval_blocked_is_runner_failure_not_test_result():
    contract = normalize_test_runner_result(
        command="python -m pytest backend/tests -q",
        status="approval_blocked",
        success=False,
        result_text="[Approval Blocked] Tool 'run_shell' was blocked.",
    )

    assert contract["runner_status"] == "failed"
    assert contract["test_status"] == "unknown"
    assert contract["environment_errors"][0]["kind"] == "tool_blocked"
    assert is_valid_test_runner_result(contract) is False


def test_pytest_truncated_head_only_output_is_inconclusive_not_runner_failure():
    contract = normalize_test_runner_result(
        command="python -m pytest backend/tests -q --tb=short --disable-warnings -r fE",
        status="failed",
        success=False,
        result_text=(
            "." * 1800
            + "\n........................................................................ [ 93%]\n"
            + "......................................................                   [100%]\n"
        ),
    )

    assert contract["runner_status"] == "inconclusive"
    assert contract["test_status"] == "unknown"
    assert contract["environment_errors"] == []
