# Deliberately broken controls (final repaired tree)

Each mutant was applied in an isolated copy and the named regression was run. Nonzero is required.

- `cheapest-first`: exit `1`; tests/test_meta_mode.py:46: AssertionError / FAILED tests/test_meta_mode.py::test_cheapest_option_is_not_best_scoring_option

- `always-act`: exit `1`; tests/test_meta_mode.py:58: AssertionError / FAILED tests/test_meta_mode.py::test_no_option_worth_cost_explicitly_chooses_abstention

- `unknown-as-zero`: exit `1`; tests/test_meta_mode.py:70: AssertionError / FAILED tests/test_meta_mode.py::test_unknown_is_not_silently_scored_as_known_zero

- `work-keyed-expense`: exit `1`; tests/test_budget.py:128: AssertionError / FAILED tests/test_budget.py::test_sequential_meta_decisions_reserve_expense_independently

- `retry-receipt-violation`: exit `1`; tests/test_meta_mode_loop.py:213: AssertionError / FAILED tests/test_meta_mode_loop.py::test_autonomous_practice_without_transfer_receipt_fails_loudly

- `unaccounted-invalid-forecast`: exit `1`; tests/test_meta_mode_loop.py:266: AssertionError / FAILED tests/test_meta_mode_loop.py::test_semantically_invalid_forecast_is_accounted_then_parked_not_retried

- `omit-post-forecast-hard-cap`: exit `1`; `test_forecast_hard_cap_is_rechecked_after_persistence_before_act` failed


- `forecast-replaces-mode-contract`: exit `1`; `test_mode_contract_cannot_be_replaced_by_forecast_instruction` failed

- `double-count-reservation-on-restart`: exit `1`; `test_reserved_meta_expense_restart_is_idempotent_near_cap` failed

- `unknown-stop-null-deref`: exit `1`; `test_unknown_and_all_blocked_terminal_decisions_are_not_goal_proposals` failed

- `oversized-expense-db-crash`: exit `1`; `test_expense_that_cannot_fit_decision_column_is_semantically_rejected` failed
