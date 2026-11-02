# Comparative Experimental Validation Plan

No full cohort run is authorized by this audit. Use current source in separate experiment directories; leave V1.9 artifacts untouched.

| Experiment | Competing explanation | Setup | Measures | Promotion rule |
|---|---|---|---|---|
| A: exploration | static retrieval locks wrong target vs tool inspection corrects it | 4 near misses + labeled analysis set; same model/context budget | correct-file/symbol recall, commands, tokens, time | improve localization without excessive context/time. |
| B: editor | reconstruction protocol causes pre-harness loss | six non-submission tasks; current action vs Aider-like exact search/replace | application, syntax, formatting/multifile preservation, harness submission | more submissions and zero new corruption. |
| C: residual loop | missing preservation causes near misses | requests-2148/2674/1963/863; current retry vs parent-preserving delta | F2P by iteration, P2P, rollback count, cost | improve full F2P; no P2P decline. |
| D: interaction | tool loop creates useful evidence | fixed pipeline vs restricted read/search/test loop | wrong-target recovery, useful observations, resolution, tokens | gain relative to equal token/command cap. |
| E: model | model capability vs system mechanics | chosen models with identical V2 lifecycle | all repair metrics plus cost | gain per resolved task, replicated. |

Pre-register task IDs, revisions, prompts, seeds, time/token/candidate budgets and acceptance rules. Inspect failures manually but never feed gold patches/test patches to the system. Escalate from unit tests -> task replay -> 4–6 task pilot -> frozen 20-task cohort only on a passed gate. Report uncertainty: 20 tasks is too small for definitive generalization.
