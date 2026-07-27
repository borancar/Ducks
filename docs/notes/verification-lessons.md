# Prove the instrument before reading its shape

Three times in one session, confident structural conclusions were drawn from
measurements the instrumentation was producing wrongly:

- `--trace-calls` read the caller's return address *after* `_on_native` had
  already advanced SP, so caller attribution was garbage — negative offsets
  pointing into the interrupt vector table. The callers were reported before
  anyone noticed.
- The verify harness ignored the handler's return value, counting every `DECLINE`
  as a mismatch (463 of them). Those counts were then used to argue an entity type
  was mis-decoded, and the native was restricted to two types. All types were
  correct.
- A debug log flag stayed set past the call being inspected, so blits from later
  unrelated calls appeared in the window. The conclusion drawn was that the
  original emitted extra draws per entity. It did not.

**Why it happens:** in each case the numbers had a plausible *shape*, and the
reasoning went from shape to conclusion without first asking whether the tool was
measuring the intended thing. Broken instrumentation produces coherent-looking
wrong answers, which is exactly what makes it dangerous.

**How to apply.** Before inferring anything from new instrumentation, prove it
measures what it claims on a case whose answer is already known. When a
measurement contradicts a careful reading of the disassembly, suspect the
measurement first — here the disassembly was right every time. And say
"unverified" rather than "verified" for a path with zero recorded executions:
`0 mismatches` on code that never ran means nothing.

## Verification that works here

- `--verify --verify-only <names>` byte-compares a native against the original
  body on every call: it runs the native into a snapshot, restores, lets the
  original body run, and compares on return.
- For game states that cannot be produced on demand, drive the guest's own code on
  synthetic input instead — `test_retire.py` is the worked example.
- For anything derived from static analysis, pin it to known answers in a test.
  `test_fn_start.py` does this for function-boundary attribution, which had been
  failing silently on large functions.

See [drawing-port-goal](drawing-port-goal.md) and
[running-a-session](running-a-session.md).
