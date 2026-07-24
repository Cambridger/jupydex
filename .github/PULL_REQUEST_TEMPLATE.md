## Summary

Describe the user-visible change and why it is needed.

## Behavior and tradeoffs

Explain the important implementation choices, compatibility impact, and safety
considerations.

## Validation

- [ ] `python -m unittest discover -s tests -v`
- [ ] `python tools/check_release.py`
- [ ] `python tools/check_links.py`
- [ ] `python -m build`

## Privacy and security

- [ ] The diff contains no real endpoint, hostname, IP address, username, path,
      token, cookie, key, terminal output, or internal project name.
- [ ] New behavior retains explicit terminal selection and does not introduce
      implicit process killing or deletion.
- [ ] Documentation and changelog are updated when behavior changes.
