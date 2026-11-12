# Security policy

## Supported versions

Security fixes go into the latest release on `main`.

| Version | Supported |
|---|---|
| 1.x (latest release) | Yes |
| Older releases | No |

## Reporting a vulnerability

**Do not open a public issue for a security problem.** Report it
privately through GitHub instead, or by email (below):

1. Open the repository's **Security** tab.
2. Click **Report a vulnerability**
   ([direct link](https://github.com/aris-space/soarr-groundstation-gnuradio/security/advisories/new)).
3. Describe the problem: the affected block or file, how to reproduce it
   (a flowgraph, a test, or the input that triggers it), and what an
   attacker could do with it.

The report stays private between you and the maintainers until a fix is
released. We reply and track progress in the advisory itself.

If you can't use GitHub, email **soarr-gs@aris-space.ch** instead, with
the same details.

## What counts as a security issue

gr-soarr builds and parses CCSDS telecommand frames, and protects them
with SDLS (AES-256-CTR encryption, AES-CMAC authentication). Examples
of what to report privately:

- a way to make the TX chain reuse an SDLS (key, counter) pair, or leak
  key material into a frame, a log, or metadata
- a frame that passes `sdls_authentication_verify` without the right key
- received data that crashes or stalls the RX chain, or lets a crafted
  frame through a check it should fail

Not a vulnerability:

- the keys built into `db_client`'s dummy mode (`TEST_CRYPT_KEY`,
  `TEST_AUTH_KEY`): they are public test keys by design
- the all-zero default `nonce` of the SDLS blocks: set your own in the
  flowgraph
- `db_client`'s dummy and YAML modes not persisting SDLS counters across
  restarts: keeping counters unique over time is the job of the external
  key database (see the README's Known issues)

For ordinary bugs, use the
[issue tracker](https://github.com/aris-space/soarr-groundstation-gnuradio/issues).
