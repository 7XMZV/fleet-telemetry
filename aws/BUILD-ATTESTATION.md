# Build attestation — revision 3

## 1. pycryptodome is pinned

```
pycryptodome==3.23.0
```

Set once in `package.py` as `PYCRYPTODOME` and used verbatim in the pip
invocation, so the pin cannot drift from what is actually installed. It is also
echoed in the build line, so every build log records it:

```
layer.zip   2.17 MB  (354 files, pycryptodome==3.23.0, manylinux2014_x86_64 / python3.13)
```

---

## 2. Artefact hashes, from a clean extraction

**Procedure**

1. `BlueICE-deployment-review-v3.zip` extracted into an **empty** directory.
2. `python package.py` run inside `1-for-review/`.
3. `lambda-source/` inside that extraction is the only build input — nothing is
   read from the wider project.

| Artefact | SHA-256 |
|---|---|
| review ZIP | `6054eed980c69739c93807a3b3ca735148ac7778361d689deda83658676ff639` |
| `function.zip` | `e9741a721705ae696a2279fd847d2fe0173628c642baf255403e141a0866ca9e` |
| `layer.zip` | `ea24803aa78b09a245450e2c39befdfd7bfe2df638951ebe7036dd1ce7e8d7a3` |

### The hashes are reproducible, which is what makes them worth publishing

Zip entries normally carry each file's modification time, so identical content
built twice produces different bytes — and a published hash proves nothing.
Both archives are now written with a fixed timestamp, fixed permissions, and
entries sorted, so an archive is a pure function of its contents.

Verified by extracting the same ZIP into **two independent empty directories**
and building each:

```
function.zip   build A == build B   IDENTICAL
layer.zip      build A == build B   IDENTICAL
```

**To confirm this yourself:**

```
mkdir check && cd check
python -c "import zipfile;zipfile.ZipFile('BlueICE-deployment-review-v3.zip').extractall('.')"
cd 1-for-review
python package.py
```

The two SHA-256 lines it prints should match the table above exactly. If they
do not, the ZIP you hold is not the one these tests were run against.

---

## 3. Test output

Run inside the clean extraction. The tests unpack `function.zip` into a
`/var/task` stand-in, so they exercise the **packaged artefact** rather than
the working tree.

```
--- 6: fixtures OFF by default ---
fixtures off -- real telemetry only
  [PASS] stage_fixtures() is a no-op when FIXTURES_PREFIX is unset
  [PASS] BLUEICE_EXTRA not set, so only real telemetry is built

--- 7: dispensing dockets are uploaded with the demo logs ---
  [PASS] deploy.py globs *.dispenses.json
  [SKIP] demo dockets not in this tree (expected in a clean extraction --
         demo data is not part of the review package)

--- 4: only one execution at a time ---
  another execution holds the lock (0s old) -- exiting
  [PASS] first caller takes the lock
  [PASS] second caller is refused while it is held
  [PASS] lock is reusable once released

--- 4: publish stages, then copies into place ---
  staged    data.js          0.00 MB
  staged    track.js         0.00 MB
  staged    index.html       0.00 MB
  published 3 file(s) from build BUILDID
  [PASS] all three files staged first
  [PASS] then copied to their final keys
  [PASS] index.html is published last
  [PASS] staging is cleaned up
  [PASS] published the three expected paths

--- 4/3: a partial build refuses to publish ---
  [PASS] partial build is refused
  [PASS] nothing was written during the refused publish

--- 3: state is written only after a successful publish ---
  [PASS] state object absent after a failed upload

--- 5: deleted source logs are pruned from state ---
  pruned 1 deleted source log(s) from state
     - device-logs/VEH_001/gone_LOG001.TXT
  [PASS] the vanished object is pruned
  [PASS] the surviving object is kept
  [PASS] non-bucket entries are left alone

--- 5: failures raise instead of returning success ---
  [PASS] a denied state read raises
  [PASS] fixtures switched on but absent raises

--- 9: the packaged artefact carries no key ---
  [PASS] no decryption key in function.zip
  [PASS] decode_log reads the key from the environment

==========================================================
  21 passed, 0 failed
==========================================================
```

**On the SKIP.** The first clean-room run reported this as a FAIL, which was a
defect in the test rather than in the code: it looked for the demonstration
dispensing dockets in the working tree, and demo data is deliberately not in
the review package. It now skips with the reason stated. The assertion that
actually covers review item 7 — that `deploy.py` uploads `*.dispenses.json` —
runs in both environments and passes in both. In the developer tree the docket
check also runs and passes (5 dockets).

That is why this run shows 21 rather than the 22 reported for revision 2.

---

## 4. Unchanged from revision 2

- Nothing has been deployed.
- No administrator operation appears in `deploy.py` — verified by scanning the
  script, not by inspection.
- The decryption key is absent from the code, the documents and both archives.
- `DECODER-VALIDATION.md` section 2 still withdraws the claim that the 100%
  logs validate the binary decoder. That correction stands.
