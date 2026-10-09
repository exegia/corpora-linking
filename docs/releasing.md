# Release setup and first publication

Version **0.1.0** is published on PyPI and its installation is verified.
See `verification.md` for artifact hashes and checks.
The repository and public package release have been authorized by the maintainer;
the public GitHub repository now exists and the initial CI run passed.
Trusted publishing succeeded in Actions run 37881455925.

## One-time owner setup

1. **exegia/corpora-linking** is now public. Its initial Homebrew CLI scaffold
   is replaced by this package through a feature PR while preserving history.
2. Allow the GitHub connector to access the new repository. The connected GitHub
   tools cannot create repositories, and direct api.github.com access is denied
   by this execution environment's proxy policy.
3. In PyPI Publishing, add a **pending publisher** with:

   | Field | Value |
   |---|---|
   | PyPI project name | `corpora-linking` |
   | GitHub owner | `exegia` |
   | Repository | `corpora-linking` |
   | Workflow filename | `publish.yml` |
   | GitHub environment | `pypi` |

4. Create the GitHub Actions environment **pypi**. Enable private vulnerability
   reporting and protect release tags/main according to the organization's policy.
   Organization approvals may be needed for workflow runs/action permissions.

Use [PyPI trusted publishing](https://docs.pypi.org/trusted-publishers/) rather
than pasting credentials into this chat or committing tokens. Pending publishers
create a project on its first successful authorized upload; they are not a name
reservation. Recheck name ownership before release.

## Release execution after setup

Follow `.github/WORKFLOW.md`: review and merge the feature PR into `dev`,
promote through `next`, and merge an appropriate release PR into `main`.
Respect existing branch and tag protections; do not push an unrelated main
history or force-push. Verify CI and the exact package version on the release
commit before creating `v0.1.0` with an authorized identity. The tag-triggered publish.yml rechecks tag/version, tests,
builds and installed-wheel behavior before uploading through GitHub OIDC.
Do not overwrite the version or reuse a failed release tag for different code.
If no files reached PyPI a failed workflow can be retried after fixing setup;
if some files were uploaded, inspect the existing artifacts before continuing.

Verify both wheel and sdist appear at https://pypi.org/project/corpora-linking/0.1.0/.
Install `corpora-linking==0.1.0` from PyPI into a fresh environment away from this
checkout; rerun portable tests and examples. Only after that succeeds should the
README/change log be updated from candidate to released and the GitHub release
be published with honest capabilities/limits. No social-media post is automated.

## Downstream order

1. In corpora-py remove `packages/linking/src/corpora_linking` from the bundled
   Hatch wheel packages, add the standalone dependency and replace the workspace
   editable source with the released dependency. Exactly one distribution owns
   `corpora_linking`. Keep native adapters and storage outside the core.
2. Use a new corpora-py version above the already published 5.0.0; never publish the
   changed local 5.0.0 feature wheel under that existing version. Verify normal
   install, upgrade/uninstall, tests, graph contracts and deployment dependency sizes.
3. Update the Homebrew CLI dependency floor against the actual new Python release,
   run all reference command tests with released packages, release the CLI, then
   update Formula/cli.rb to the actual CLI release tarball/checksum. The separate
   stable v2.1.0 formula catch-up does not ship the unreleased reference commands.
4. Prepare the authenticated Python API and corpora-web typed client. A deployed
   API requires separate inventory/entitlement/database configuration; a library
   release does not provision or deploy a service.

These steps intentionally wait for the core's real release. No downstream dependency
was changed to an unavailable package, and no live Supabase changes were performed.
