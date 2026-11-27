from . import models
from . import schemas
from . import routers
from . import wizards


def add_folio_push_columns(cr):
    """Create the BookAI columns of pms_folio before the ORM does, so that
    installing or upgrading does not compute them for every existing folio:
    they start empty and only change with the folio."""
    cr.execute(
        """
        ALTER TABLE pms_folio
            ADD COLUMN IF NOT EXISTS bookai_known boolean,
            ADD COLUMN IF NOT EXISTS bookai_push_pending boolean
        """
    )


def pre_init_hook(cr):
    add_folio_push_columns(cr)
