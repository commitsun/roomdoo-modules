# Copyright 2021 Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

PROPERTY_MODEL = "pms.property"


class QueueJob(models.Model):
    _inherit = "queue.job"

    pms_property_id = fields.Many2one(
        comodel_name=PROPERTY_MODEL,
        string="Property",
        readonly=True,
        index=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        """Stamp the property on creation.

        This used to be declared as a stored computed field without a
        ``compute`` method, so it was never populated and the Property column,
        filter and group-by of the job views were always empty. It is resolved
        once, at creation, instead of as a compute: the jobs table is large and
        historical rows carry no property anyway.
        """
        jobs = super().create(vals_list)
        for job in jobs.filtered(lambda job: job._is_channel_job()):
            pms_property = job._channel_job_pms_property()
            if pms_property:
                job.pms_property_id = pms_property.id
        return jobs

    def _is_channel_job(self):
        """True when this job was enqueued on a channel binding or a backend.

        ``queue.job`` is inherited instance-wide, so ``create`` runs for every
        job there is: mail, invoicing, smartlocks. This decides from the stored
        ``model_name`` column and the registry, and it has to come before
        ``_channel_job_pms_property``, which reads ``records``, ``args`` and
        ``kwargs``: those are ``JobSerialized``, so touching them costs
        deserializing the whole payload of a job that could never fill a field
        only channel jobs can fill.

        Both kinds of model are accepted so that no job the resolution used to
        reach is dropped. Bindings cover the normal path -- the exporter and
        importer components delay ``self.model``, the listeners and the connect
        wizard delay a binding recordset. Backends are matched separately
        because they are not ``channel.binding`` subclasses: a vendor backend
        delegates to ``channel.backend`` through ``_inherits``.
        """
        self.ensure_one()
        registry = self.env.registry
        model_cls = registry.get(self.model_name)
        if model_cls is None:
            return False
        binding_cls = registry.get("channel.binding")
        if binding_cls is not None and issubclass(model_cls, binding_cls):
            return True
        return self.model_name == "channel.backend" or "channel.backend" in (
            model_cls._inherits or {}
        )

    def _channel_job_pms_property(self):
        """Best-effort resolution of the property a channel job acts on.

        Channel jobs are enqueued either on a binding recordset (``records``,
        which reaches the property through its backend) or with the backend
        itself passed as an argument. Anything else leaves the field empty.
        """
        self.ensure_one()
        candidates = [self.records]
        candidates.extend(self.args or ())
        candidates.extend((self.kwargs or {}).values())
        for candidate in candidates:
            if not isinstance(candidate, models.BaseModel) or not candidate:
                continue
            record = candidate[:1]
            if record._name == PROPERTY_MODEL:
                return record
            # Backends and the records scoped to a single property expose it
            # directly; bindings reach it through their backend.
            for path in ("pms_property_id", "backend_id.pms_property_id"):
                pms_property = self._traverse(record, path)
                if pms_property:
                    return pms_property
        return self.env[PROPERTY_MODEL].browse()

    @api.model
    def _traverse(self, record, path):
        """Follow a dotted Many2one path, returning an empty recordset if any
        step is missing on the model or unset on the record."""
        for field_name in path.split("."):
            field = record._fields.get(field_name)
            if field is None or field.type != "many2one":
                return self.env[PROPERTY_MODEL].browse()
            record = record[field_name][:1]
            if not record:
                return self.env[PROPERTY_MODEL].browse()
        if record._name != PROPERTY_MODEL:
            return self.env[PROPERTY_MODEL].browse()
        return record
