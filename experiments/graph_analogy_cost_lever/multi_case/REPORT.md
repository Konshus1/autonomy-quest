# Multi-case analogy pursuit report

## Verdict

**Two new existence triples were found across 32 new one-shot tasks.** Including the prior Chao1 case, the frozen corpus now contains **three triples across 33 tasks**. The requested three-to-five *new* cases was not reached. The two new realized frontier/structural CLI-envelope ratios were 12.97x and 12.20x, so this extension also does **not** earn the requested ~40x aggregate claim.

The two new positives are narrow, content-level claims: structural retrieval supplied a load-bearing calculation that Haiku baseline and Haiku with the surface-selected source missed, while Fable produced it unaided. This is not whole-answer parity, repeated-run reliability, a population benchmark, or a causal isolation of retrieval from the answer-bearing source payload.

## Every new task tried

| task | target -> structural source domain | result | why |
|---|---|---|---|
| `canary_wilson` | ml_operations -> election_polling | **NEITHER** | Protocol-invalid candidate: structural used z=1.96 for a policy phrased as a one-sided 95% claim; the frontier correctly used exact one-sided binomial inference. Not counted despite the same no-rollout decision. |
| `zero_failure_rule_three` | storage_operations -> clinical_safety | **NEITHER** | Baseline and surface independently computed an exact zero-event upper bound near 9.5-9.8% and rejected certification. |
| `gpu_newsvendor` | cloud_capacity -> perishable_inventory | **NEITHER** | Baseline and surface both derived the newsvendor 0.90 fractile and about 126 slots. |
| `worker_erlang_c` | ci_operations -> call_center_staffing | **NEITHER** | Baseline and surface both ran Erlang C and chose four workers. |
| `firmware_hypergeometric` | hardware_quality -> archaeological_sampling | **NEITHER** | Structural selected the right exact model but reported the wrong 24-25-device boundary instead of the preregistered 22/23 breakpoint. |
| `redteam_neyman` | security_testing -> household_survey | **NEITHER** | Baseline and surface both derived Neyman allocation 43/51/26. |
| `release_sprt` | software_release -> industrial_quality_control | **NEITHER** | Baseline and surface both computed Wald SPRT boundaries and continued sampling. |
| `defect_chain_ladder` | software_quality -> insurance_reserving | **FOUND_TRIPLE** | Baseline used a nonpreregistered discovery-curve extrapolation and surface omitted the chain-ladder calculation; structural computed 49.5, 33, and 17.5, and frontier reached the same projection unaided. |
| `machine_edf_dbf` | industrial_scheduling -> avionics_realtime | **NEITHER** | Baseline independently found the t=8 demand overload of 10>8. |
| `warehouse_network_calculus` | warehouse_operations -> packet_networking | **NEITHER** | Baseline and surface both computed the 180-order backlog and 4.4-unit delay. |
| `hospital_banker` | hospital_operations -> operating_systems | **NEITHER** | Baseline found that no completion can start after the tentative grant; surface explicitly ran the Banker safety check. |
| `bay_erlang_b` | field_service -> telephone_trunking | **NEITHER** | All cheap arms selected Erlang B and 18 bays. |
| `sequence_german_tank` | threat_intelligence -> wartime_production | **NEITHER** | Baseline and surface both computed the bias-corrected estimate 107. |
| `cloud_lower_bound_flow` | cloud_networking -> irrigation_planning | **NEITHER** | Baseline supplied the lower-bound residual transformation and a saturation certificate; surface also reconstructed most of it. |
| `api_kingman` | api_operations -> factory_queueing | **NEITHER** | Baseline was wrong, but the surface arm independently selected Kingman and computed 8.5 minutes. |
| `build_kaplan_meier` | build_operations -> clinical_survival | **NEITHER** | Baseline and surface both computed Kaplan-Meier S(4)=5/12. |
| `snapshot_young` | data_pipeline -> hpc_checkpointing | **NEITHER** | Baseline was wrong, but surface derived the first-order optimum of 20 minutes. |
| `testslot_dhondt` | security_governance -> parliamentary_apportionment | **NEITHER** | Structural named the method but never executed the allocation; baseline also produced a wrong final allocation. |
| `shard_buhlmann` | cloud_reliability -> insurance_credibility | **NEITHER** | Baseline and surface both computed Bühlmann credibility estimate 0.15. |
| `incident_croston` | site_reliability -> spare_parts_inventory | **NEITHER** | Structural supplied Croston state updates but omitted the preregistered numeric forecast; baseline also omitted it and surface used a different initialization. |
| `mitigation_mantel_haenszel` | cloud_experimentation -> epidemiology | **NEITHER** | Baseline independently computed the adjusted odds ratio 0.459. |
| `classifier_mcnemar` | machine_learning_evaluation -> paired_medical_diagnosis | **NEITHER** | Baseline and surface selected McNemar and computed the load-bearing statistic. |
| `monitor_sidak` | observability -> industrial_sensor_testing | **NEITHER** | All cheap arms derived the Sidak threshold near 0.00256. |
| `pipeline_johnson` | deployment_operations -> two_machine_manufacturing | **NEITHER** | Baseline and surface both ran Johnson rule, produced D-A-C-B-E, and got makespan 36. |
| `autoscaler_ziegler_nichols` | database_control -> chemical_process_control | **NEITHER** | Baseline and surface both produced the Ziegler-Nichols gains. |
| `robot_engset` | robotics_maintenance -> finite_subscriber_telephony | **FOUND_TRIPLE** | Baseline and surface confused time congestion C(N,k) with per-request Engset blocking and chose seven; structural used C(N-1,k), obtained 10.7%/3.44%, chose six, and frontier did the same unaided. |
| `batch_wagner_whitin` | compute_batching -> inventory_lot_sizing | **NEITHER** | Baseline and surface found the optimal period-1/period-3 plan and cost 23; structural failed to execute its dynamic program. |
| `keyrotation_renewal_residual` | security_operations -> public_transit | **NEITHER** | All cheap arms computed the stationary residual life as 10 days. |
| `device_linear_counting` | threat_monitoring -> streaming_databases | **NEITHER** | Baseline and surface independently inverted zero occupancy and estimated about 1609. |
| `latency_cornish_fisher` | service_reliability -> financial_risk | **NEITHER** | Baseline and surface both applied Cornish-Fisher and computed about 119.3 ms. |
| `cache_shapley` | cloud_cost_allocation -> cooperative_game_theory | **NEITHER** | Structural named Shapley but omitted the preregistered 5/4/3 allocation; no structural success. |
| `audit_horvitz_thompson` | security_auditing -> forest_inventory | **NEITHER** | Baseline and surface both computed the inverse-probability total 460. |

## Found-case blind scores

| case | judge | baseline | surface | structural | frontier |
|---|---|---:|---:|---:|---:|
| `defect_chain_ladder` | fable | 2 | 1 | 4 | 5 |
| `defect_chain_ladder` | codex | 1 | 1 | 3 | 5 |
| `robot_engset` | fable | 3 | 3 | 5 | 5 |
| `robot_engset` | codex | 3 | 3 | 5 | 5 |

The chain-ladder structural answer got the required projected ultimates and 17.5 unreported-defect core, but judges deducted for using equal cohort ratios rather than explicitly writing the preregistered volume-weighted expression. The Engset case was clean: both judges scored structural/frontier 5/5 and flagged baseline/surface as fatal because they used time congestion instead of per-request congestion.

## Prompt symmetry and frozen hashes

All 32 cases use one generated neutral template. Its SHA-256 is `875c0c2006cb385805e109657c4c71551107c0ef23307db0cd5518028848d2a9`. After replacing only the `analogue_content` value with a sentinel, all four prompts for each case have the same normalized hash; baseline and frontier prompts are byte-identical. The prompts never name surface, lexical, structural, graph, or selection regime. `manifest.json` contains all 128 prompt SHAs; every arm also has a colocated `prompt_sha256.txt`.

Found-case prompt SHAs:

| case | baseline/frontier | surface | structural |
|---|---|---|---|
| `defect_chain_ladder` | `665d6b2d1fe257f48bd84edf40707d8b87e22c0e3c7ce4ec92c6a05af29667ab` | `2d7b17681a0840762fecd7b77722d6a0ec8a3373698970a6bf61f9a4d6b9d16c` | `8d8233459622efbf2d3d51014b89cc1398a6dc702865a53c1d2a6b60d582f92a` |
| `robot_engset` | `b27141351dc1f70cd00e86128afbbdae3b84e7b0f2ee2c2b975cb93db2f9e2fc` | `e041dea3ded7676118fa99c9932e60d2d72f588453d38b3c51d525ebf9b94936` | `35846d9b90761c0a62bab9105c33a439a7c945a11e758e75a02aa03b1d0aa743` |

Preregistration commits, each made before its batch calls: `90a93db`, `ca3ac02`, `8cf1ffb`, `c549b77`, `740ce31`, `62d27ca`. These prove repository ordering, not external timestamp attestation. Source episodes and graphs were hand-constructed for their targets; the result is conditional on those three-item candidate libraries and must not be described as held-out corpus retrieval.

## Reproduction

```bash
uv run python experiments/graph_analogy_cost_lever/run_multi_case.py \
  --output experiments/graph_analogy_cost_lever/multi_case/receipt.json
uv run pytest tests/test_structural_analogy_cost_lever.py -q
```

The runner makes no network calls. It recomputes prompt/case/graph hashes, reconstructs every prompt from the canonical template, verifies raw subscription envelope results and model IDs, reruns surface and structural retrieval, validates the two found numeric cores, validates both blind-judge schemas/maps/scores, and regenerates the aggregate.

## Honest aggregate

> Two new triples across 32 new tasks tried. Including the prior Chao1 case: three triples across 33 tasks. The attempted extension did not earn three-to-five new cases and did not reproduce a ~40x realized-envelope ratio; the two new positive cases realized 12.97x and 12.20x.
