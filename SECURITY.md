# Security policy

muscle-memory runs scripts on your machine, so security reports matter.

**Please report vulnerabilities privately** with GitHub's [private vulnerability reporting](https://github.com/kabirshah4/muscle-memory/security/advisories/new), not as a public issue. Include steps to reproduce and the version (`.claude-plugin/plugin.json`).

In scope, for example:
- anything that makes a script run with no model call without the user approving it (`mm promote`) or naming it (`mm <key>`)
- input that gets past the argument checks for pattern slots
- ways to change an approved script without approval being revoked

You'll get a reply within a week. Only the latest release gets fixes.
