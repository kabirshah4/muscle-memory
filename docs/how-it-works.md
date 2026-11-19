# How it works, in detail

## Instincts

An instinct is a saved script that runs inside the prompt hook, before the model is called. It costs 0 tokens.

**Becoming eligible.** A script reflex becomes eligible when all of these hold:

1. **2 clean runs in a row through `mm run`** (`MUSCLE_MEMORY_INSTINCT_AFTER`). A run counts if it had no inputs, or if every input is a word copied from your prompt. For example, "deploy the worker to staging" run with `staging` teaches the pattern `deploy the worker to {1}`. A run whose inputs aren't in the prompt resets the count.
2. **The output was checked.** Exit code 0 only means the script didn't crash. In the benchmark, one compiled script ran cleanly and still wrote the wrong report. So the first time Claude runs an unchecked script, the hint asks it to compare the output with what you asked for, then run `mm verify <key>` (correct) or `mm fail <key>` (wrong). Re-saving the script clears the check.
3. **There's a wording to approve.** A prompt was recorded for at least one clean run.

**Approval.** `mm promote <key>` works only in an interactive terminal. It shows the script, its sha256 hash, and the wordings and patterns it will answer to, then asks you to type `yes`. When a reflex becomes eligible, the hook tells **you** directly. That note never reaches Claude, so it costs no tokens.

**Firing.** From then on, a prompt fires the instinct only if:
- its words match an approved wording in the same order (filler like "the" is ignored), or it follows an approved pattern word for word
- it has 12 words or fewer, no question mark, and no negation ("don't", "not", "fix", ...)
- each pattern slot holds a single plain word (letters, digits, `. _ / : @ + = -`, no `..`). Slot words are passed to the script as arguments, never through a shell.

**Revoking.** Editing or re-saving the script revokes approval. A failed instinct is demoted and Claude takes over with the error.

## Hit rate

Exact matching is safe but strict. `mm list` shows, for each instinct, `free-runs` (prompts it answered with no model call) and `near-misses` (prompts that matched the task but not an approved wording, so they went to the model), plus the hit rate. If near-misses are high, run the task through Claude with your usual wording a couple of times. Those wordings become approvable at your next `mm promote`.
