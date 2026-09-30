"""Ticket flow metrics API.

Routed under the ORG rather than under a Middleware team, on purpose. A
Middleware Team is a group of repositories; the axis these metrics run on is
the GitHub team — a group of PEOPLE — and Zinc's squads share repositories.
The web-server resolves the selected GitHub team to its Shortcut team ids
from github_teams.json and passes them in the ticket_filter, so this layer
stays provider-shaped and needs no new table.

Registered as its own blueprint, so the only change to an existing file in
Layer 2 is two lines in app.py.
"""

import json
from datetime import datetime

from flask import Blueprint
from voluptuous import All, Coerce, Optional, Required, Schema

from mhq.api.request_utils import queryschema
from mhq.api.resources.ticket_resources import adapt_flow_metrics
from mhq.service.query_validator import get_query_validator
from mhq.service.tickets.analytics import get_ticket_analytics_service
from mhq.service.tickets.ticket_filter import apply_ticket_filter

app = Blueprint("tickets", __name__)


@app.route("/orgs/<org_id>/ticket_flow", methods={"GET"})
@queryschema(
    Schema(
        {
            Required("from_time"): All(str, Coerce(datetime.fromisoformat)),
            Required("to_time"): All(str, Coerce(datetime.fromisoformat)),
            Optional("ticket_filter"): All(str, Coerce(json.loads)),
        }
    ),
)
def get_ticket_flow_metrics(
    org_id: str,
    from_time: datetime,
    to_time: datetime,
    ticket_filter: dict = None,
):
    query_validator = get_query_validator()
    query_validator.org_validator(org_id)
    interval = query_validator.interval_validator(from_time, to_time)

    metrics = get_ticket_analytics_service().get_flow_metrics(
        org_id=org_id,
        interval=interval,
        ticket_filter=apply_ticket_filter(ticket_filter),
    )

    return adapt_flow_metrics(metrics)
