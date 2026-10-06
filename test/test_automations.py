"""Graph model, typed operations, compiler, executor and local status changes."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.agents.builder.compiler import compile_draft
from app.core.agents.builder.graph_ops import GraphOpError, apply_op
from app.core.agents.builder.schemas import FlowDraft
from app.core.automations.executor import AutomationExecutor, evaluate
from app.data.models.automation import (
    ActionNode,
    Automation,
    AutomationGraph,
    ConditionNode,
    Edge,
    RunTrigger,
    TriggerNode,
)
from test.fakes import default_handler


def _draft() -> FlowDraft:
    msg = default_handler([], ["FlowDraft"])
    return FlowDraft.model_validate(msg.tool_calls[0]["args"])


def test_compile_and_layout() -> None:
    g = compile_draft(_draft())
    assert g.trigger().id == "trigger"
    xs = {n.id: n.position.x for n in g.nodes}
    assert xs["trigger"] < xs["analisi"] < xs["anomalie"] < xs["avvisa"]
    ys = {n.id: n.position.y for n in g.nodes}
    assert ys["avvisa"] < ys["archivia"]  # "vero" above "falso"


def test_graph_validation_rules() -> None:
    t = TriggerNode(id="t", label="T")
    c = ConditionNode(id="c", label="C", field="x")
    a = ActionNode(id="a", label="A")
    with pytest.raises(ValidationError):
        AutomationGraph(nodes=[t, c, a], edges=[Edge(id="1", source="t", target="c"), Edge(id="2", source="c", target="a")])
    with pytest.raises(ValidationError):  # loop
        AutomationGraph(nodes=[t, a, ActionNode(id="b", label="B")], edges=[
            Edge(id="1", source="t", target="a"), Edge(id="2", source="a", target="b"), Edge(id="3", source="b", target="a")])
    with pytest.raises(ValidationError):  # two triggers
        AutomationGraph(nodes=[t, TriggerNode(id="t2", label="T2")], edges=[])


def test_operations() -> None:
    g = compile_draft(_draft())
    g2 = apply_op(g, {"op": "add_node", "node": {"kind": "notification", "label": "Avvisa Acquisti", "channel": "gmail", "recipients": "acquisti@officine.it"},
                      "after": "anomalie", "branch": "vero"})
    assert any(n.label == "Avvisa Acquisti" for n in g2.nodes)
    g3 = apply_op(g2, {"op": "remove_node", "node_id": "analisi"})
    assert any(e.source == "trigger" and e.target == "anomalie" for e in g3.edges)  # reconnected
    with pytest.raises(GraphOpError):
        apply_op(g, {"op": "remove_node", "node_id": "trigger"})
    with pytest.raises(GraphOpError):
        apply_op(g, {"op": "add_node", "node": {"kind": "action", "label": "X"}, "after": "anomalie", "branch": "forse"})
    g4 = apply_op(g, {"op": "set_trigger", "cron": "0 7 * * *", "label": "Ogni mattina"})
    assert g4.trigger().cron == "0 7 * * *"


def test_condition_evaluation() -> None:
    c = ConditionNode(id="c", label="OEE", field="oee", op="<", value=75)
    assert evaluate(c, {"oee": 71.5}) is True
    assert evaluate(c, {"oee": "80"}) is False
    assert evaluate(ConditionNode(id="d", label="x", field="r.n", op=">", value=0), {"r": {"n": 2}}) is True


async def test_executor_prova_runs_locally(services, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from test.conftest import ORG, USER, login

    login(services)
    a = Automation(id="auto_x", org_id=ORG, title="Confronto revisioni", graph=compile_draft(_draft()))
    ex = AutomationExecutor(services.agent_context(ORG, USER), tmp_path)
    run = await ex.run(a, RunTrigger.prova)
    assert run.status.value == "success", run.error
    by_node = {e.node_id: e for e in run.log}
    assert by_node["anomalie"].output["outcome"] == "vero"  # fake agent found 2 anomalies
    assert by_node["avvisa"].status == "skipped"  # a Gmail notification: the connector is not set up → skipped, the run goes on
    assert "archivia" not in by_node  # the other branch did not run


async def test_executor_writes_pdf_on_false_branch(services, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from test.conftest import ORG, USER, login

    login(services)
    g = apply_op(compile_draft(_draft()), {"op": "update_node", "node_id": "anomalie", "changes": {"value": 10}})
    a = Automation(id="auto_y", org_id=ORG, title="Report", graph=g)
    run = await AutomationExecutor(services.agent_context(ORG, USER), tmp_path).run(a, RunTrigger.prova)
    pdf = next(e for e in run.log if e.node_id == "archivia")
    assert pdf.status == "ok" and pdf.output.endswith(".pdf")


def test_status_and_test_run_api(client) -> None:  # type: ignore[no-untyped-def]
    from test.test_builder_chat import _discovered

    a = _discovered(client)
    r = client.post(f"/api/v1/automations/{a['id']}/test").json()
    assert r["run"]["status"] == "success"
    assert len(client.get(f"/api/v1/automations/{a['id']}/runs").json()) == 1
    act = client.post(f"/api/v1/automations/{a['id']}/status", json={"status": "attiva"}).json()
    assert act["status"] == "attiva"
    view = client.get(f"/api/v1/automations/{a['id']}").json()
    assert view["next_run"]  # scheduled locally (cron from the trigger)
    client.post(f"/api/v1/automations/{a['id']}/status", json={"status": "caricata"})
    assert client.get(f"/api/v1/automations/{a['id']}").json()["next_run"] is None
