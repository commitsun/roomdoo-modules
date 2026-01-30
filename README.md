# Roomdoo Modules

[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Odoo Version](https://img.shields.io/badge/odoo-16.0-blue)](https://github.com/odoo/odoo/tree/16.0)
[![Tests](https://github.com/commitsun/roomdoo-modules/actions/workflows/test.yml/badge.svg?branch=16.0)](https://github.com/commitsun/roomdoo-modules/actions/workflows/test.yml)

## Overview

This repository contains modules that extend the functionality of the [OCA/pms](https://github.com/OCA/pms) (Property Management System) for Odoo 16.0.

The modules included here provide additional features and improvements designed to meet specific needs in hotel property management that go beyond the core OCA/pms capabilities.

## ⚠️ Important: Development Dependencies

**This repository may depend on code that is not yet available in official OCA repositories.** This includes:

- **Pending bugfixes**: Bug corrections that are currently under review as Pull Requests in OCA repositories
- **New features**: Modules or functionalities being developed in our own forks before upstream contribution
- **Modified versions**: Specific adaptations of OCA modules that haven't yet been integrated upstream

### Dependency Management

All external dependencies, including those not yet in official OCA repositories, are documented in the git-aggregator configuration file:
```
.github/repos.yaml
```

**Please consult this file for the complete list of dependencies**, including:
- Source repositories (official OCA, our forks, third-party)
- Specific branches and commits
- Pending Pull Requests
- Custom modifications

#### Python Dependencies

Additional Python packages required by these modules are listed in:
```
requirements.txt
```

Make sure to install these dependencies before using the modules:
```bash
pip install -r requirements.txt
```

This approach allows us to work with cutting-edge features and bugfixes while maintaining a clear record of all dependencies for reproducible installations.

## License

This project is licensed under the [GNU Affero General Public License v3.0](LICENSE).

## Maintainer

This repository is maintained by CommitSun.

---

**Note**: Always check `.github/repos.yaml` for the most up-to-date dependency information before installation or deployment.
