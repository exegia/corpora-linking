# Security

The core does not authenticate users or perform I/O. Consumers must authorize
access and verify the origin/version of supplied snapshots before resolving links.
Never treat arbitrary input review/publication fields as trusted approval.

Do not include credentials, private documents or personal data in public issues.
If GitHub private vulnerability reporting is enabled, use the repository's Security
page to report a suspected vulnerability privately. Otherwise contact the repository
owner using a private channel rather than posting exploit details or secrets.

During 0.x, fixes target the latest release. No broader support commitment exists.
