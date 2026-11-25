# V2.2 post-fix regression — per-case trace taxonomy

source report: `outputs/module_c/v23-postfix-armD/report.json` (label=`post-fix regression (V2.3 close-out)`)

Classification is evidence-based and does not change any score. Every case keeps its own trace file under the trace directory.

| bucket | cases |
|---|---:|
| passed | 44 |
| model_extraction | 0 |
| rule_misread | 1 |
| business_rejection | 0 |
| measurement | 1 |
| other | 1 |

## by arm

| arm | passed | model_extraction | rule_misread | business_rejection | measurement | other |
|---|---:|---:|---:|---:|---:|---:|
| harness_opt | 44 | 0 | 1 | 0 | 1 | 1 |

## per-case detail

| case | arm | rep | bucket | evidence | failures |
|---|---|---|---|---|---|
| V21-C-01 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-C-02 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-C-03 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-C-04 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-C-05 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-V-01 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-V-02 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-V-03 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-V-04 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-V-05 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-U-01 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-U-02 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-U-03 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-U-04 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-U-05 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-N-01 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-N-02 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-N-03 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-N-04 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-N-05 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-E-01 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-E-02 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-E-03 | harness_opt | 4 | rule_misread | deterministic layer changed/dropped an operation or task | task_types: missing ['optimize']; got []; params: budget_units expected 2 got None |
| V21-E-04 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-M-01 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-M-02 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-M-03 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-M-04 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-X-01 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-X-02 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-X-03 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-X-04 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-X-05 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-I-01 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-I-02 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-I-03 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-I-04 | harness_opt | 4 | other | no bucket matched; review the failures verbatim | no_result: produced a successful assess result; clarify: answer neither asked for the missing input nor explained the refusal |
| V21-I-05 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-F-01 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-F-02 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-F-03 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-F-04 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-F-05 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-B-01 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-B-02 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-B-03 | harness_opt | 4 | passed | no failure recorded by the frozen grader | - |
| V21-B-04 | harness_opt | 4 | measurement | correct policy refusal not recognised by frozen text heuristic | clarify: answer neither asked for the missing input nor explained the refusal |
