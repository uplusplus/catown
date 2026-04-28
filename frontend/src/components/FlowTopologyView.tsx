import type { CSSProperties } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Bot, Boxes, BrainCircuit, Crown, Globe, Monitor, Server, UserRound, Wrench } from "lucide-react";
import type { LucideIcon } from "lucide-react";

export type FlowTopologyStatus = "idle" | "active" | "warning" | "error";
export type FlowTopologyNodeKind =
  | "entry"
  | "client"
  | "gateway"
  | "runtime"
  | "agent"
  | "llm"
  | "tool"
  | "memory"
  | "web"
  | "approval"
  | "collaboration";

export type FlowTopologyMetric = {
  label: string;
  value: string;
};

export type FlowTopologyNode = {
  id: string;
  lane: number;
  order: number;
  kind: FlowTopologyNodeKind;
  preferredWidth?: number;
  singleRowMetrics?: boolean;
  title: string;
  subtitle?: string;
  badge?: string;
  status: FlowTopologyStatus;
  metrics?: FlowTopologyMetric[];
  chips?: string[];
  preview?: string;
};

export type FlowTopologyEdge = {
  id: string;
  from: string;
  to: string;
  label: string;
  detail?: string;
  volume: number;
  status: FlowTopologyStatus;
  active?: boolean;
};

export type FlowTopologyGraph = {
  laneLabels: string[];
  nodes: FlowTopologyNode[];
  edges: FlowTopologyEdge[];
};

type FlowTopologyViewProps = {
  graph: FlowTopologyGraph;
  compact?: boolean;
  className?: string;
};

type FlowLane = {
  lane: number;
  label: string;
  key: string;
  nodes: FlowTopologyNode[];
  preferredWidth: number;
};

type FlowLaneGroup = {
  id: string;
  lanes: FlowLane[];
  preferredWidth: number;
};

type NodeBounds = {
  left: number;
  top: number;
  width: number;
  height: number;
  right: number;
  bottom: number;
  centerX: number;
  centerY: number;
};

function kindVisual(kind: FlowTopologyNodeKind): { Icon: LucideIcon; accentClass: string } {
  switch (kind) {
    case "entry":
      return { Icon: UserRound, accentClass: "flow-node--entry" };
    case "client":
      return { Icon: Monitor, accentClass: "flow-node--client" };
    case "gateway":
      return { Icon: Server, accentClass: "flow-node--gateway" };
    case "runtime":
      return { Icon: BrainCircuit, accentClass: "flow-node--runtime" };
    case "agent":
      return { Icon: Bot, accentClass: "flow-node--agent" };
    case "llm":
      return { Icon: BrainCircuit, accentClass: "flow-node--llm" };
    case "memory":
      return { Icon: Boxes, accentClass: "flow-node--memory" };
    case "web":
      return { Icon: Globe, accentClass: "flow-node--web" };
    case "approval":
      return { Icon: Crown, accentClass: "flow-node--approval" };
    case "collaboration":
      return { Icon: Bot, accentClass: "flow-node--collaboration" };
    case "tool":
    default:
      return { Icon: Wrench, accentClass: "flow-node--tool" };
  }
}

function statusClass(status: FlowTopologyStatus) {
  return `is-${status}`;
}

function edgeStrokeWidth(volume: number, maxVolume: number) {
  if (maxVolume <= 0) return 2;
  const ratio = Math.max(0, Math.min(volume / maxVolume, 1));
  return 2 + ratio * 4;
}

function buildEdgePath(from: NodeBounds, to: NodeBounds) {
  const startX = from.right;
  const startY = from.centerY;
  const endX = to.left;
  const endY = to.centerY;
  const distance = Math.max(endX - startX, 80);
  const control = Math.max(70, distance * 0.42);
  return `M ${startX} ${startY} C ${startX + control} ${startY}, ${endX - control} ${endY}, ${endX} ${endY}`;
}

function laneKey(label: string) {
  return label.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-");
}

function lanePreferredWidth(laneNodes: FlowTopologyNode[], compact: boolean) {
  const fallback = compact ? 236 : 296;
  return laneNodes.reduce((maxWidth, node) => Math.max(maxWidth, node.preferredWidth ?? fallback), fallback);
}

function mergeLaneGroups(groups: FlowLaneGroup[], leftKey: string, rightKey: string) {
  const leftIndex = groups.findIndex((group) => group.lanes.some((lane) => lane.key === leftKey));
  const rightIndex = groups.findIndex((group) => group.lanes.some((lane) => lane.key === rightKey));
  if (leftIndex < 0 || rightIndex < 0 || leftIndex === rightIndex) return groups;

  const start = Math.min(leftIndex, rightIndex);
  const end = Math.max(leftIndex, rightIndex);
  const mergedLanes = [...groups[start].lanes, ...groups[end].lanes].sort((left, right) => left.lane - right.lane);
  const mergedGroup: FlowLaneGroup = {
    id: mergedLanes.map((lane) => lane.key).join("+"),
    lanes: mergedLanes,
    preferredWidth: Math.max(...mergedLanes.map((lane) => lane.preferredWidth)),
  };

  return [
    ...groups.slice(0, start),
    mergedGroup,
    ...groups.slice(start + 1, end),
    ...groups.slice(end + 1),
  ];
}

function laneGroupsWidth(groups: FlowLaneGroup[], gap: number) {
  return groups.reduce((total, group) => total + group.preferredWidth, 0) + Math.max(0, groups.length - 1) * gap;
}

function buildAdaptiveLaneGroups(lanes: FlowLane[], containerWidth: number, compact: boolean) {
  const gap = compact ? 12 : 16;
  const horizontalPadding = compact ? 28 : 36;
  const availableWidth = containerWidth > 0 ? Math.max(containerWidth - horizontalPadding, 0) : Number.POSITIVE_INFINITY;

  let groups: FlowLaneGroup[] = lanes.map((lane) => ({
    id: lane.key,
    lanes: [lane],
    preferredWidth: lane.preferredWidth,
  }));

  const mergePriority: Array<[string, string]> = [
    ["capabilities", "outside"],
    ["entry", "client"],
    ["client", "platform"],
  ];

  for (const [leftKey, rightKey] of mergePriority) {
    if (laneGroupsWidth(groups, gap) <= availableWidth) break;
    groups = mergeLaneGroups(groups, leftKey, rightKey);
  }

  return groups;
}

export function FlowTopologyView({ graph, compact = false, className }: FlowTopologyViewProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const nodeRefs = useRef(new Map<string, HTMLDivElement>());
  const [bounds, setBounds] = useState<Record<string, NodeBounds>>({});
  const [containerWidth, setContainerWidth] = useState(0);

  const lanes = useMemo<FlowLane[]>(() => {
    const bucket = new Map<number, FlowTopologyNode[]>();
    graph.nodes.forEach((node) => {
      const laneNodes = bucket.get(node.lane) ?? [];
      laneNodes.push(node);
      bucket.set(node.lane, laneNodes);
    });
    return [...bucket.entries()]
      .sort((left, right) => left[0] - right[0])
      .map(([lane, nodes]) => ({
        lane,
        label: graph.laneLabels[lane] ?? `Lane ${lane + 1}`,
        key: laneKey(graph.laneLabels[lane] ?? `Lane ${lane + 1}`),
        nodes: [...nodes].sort((left, right) => left.order - right.order),
        preferredWidth: lanePreferredWidth(nodes, compact),
      }));
  }, [compact, graph.laneLabels, graph.nodes]);

  const laneGroups = useMemo(
    () => buildAdaptiveLaneGroups(lanes, containerWidth, compact),
    [compact, containerWidth, lanes],
  );

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    let frameId = 0;
    const measure = () => {
      frameId = 0;
      const rootRect = container.getBoundingClientRect();
      setContainerWidth(rootRect.width);
      const nextBounds: Record<string, NodeBounds> = {};
      graph.nodes.forEach((node) => {
        const element = nodeRefs.current.get(node.id);
        if (!element) return;
        const rect = element.getBoundingClientRect();
        const left = rect.left - rootRect.left;
        const top = rect.top - rootRect.top;
        nextBounds[node.id] = {
          left,
          top,
          width: rect.width,
          height: rect.height,
          right: left + rect.width,
          bottom: top + rect.height,
          centerX: left + rect.width / 2,
          centerY: top + rect.height / 2,
        };
      });
      setBounds(nextBounds);
    };

    const scheduleMeasure = () => {
      if (frameId) return;
      frameId = window.requestAnimationFrame(measure);
    };

    scheduleMeasure();

    if (typeof ResizeObserver === "undefined") return;

    const observer = new ResizeObserver(() => scheduleMeasure());
    observer.observe(container);
    nodeRefs.current.forEach((element) => observer.observe(element));

    return () => {
      if (frameId) {
        window.cancelAnimationFrame(frameId);
      }
      observer.disconnect();
    };
  }, [compact, graph.nodes]);

  const maxEdgeVolume = useMemo(
    () => graph.edges.reduce((maxVolume, edge) => Math.max(maxVolume, edge.volume), 0),
    [graph.edges],
  );

  const isZoneGroup = (group: FlowLaneGroup) =>
    group.lanes.some((lane) => lane.key === "capabilities" || lane.key === "outside");

  const renderNode = (node: FlowTopologyNode, laneLabel?: string) => {
    const visual = kindVisual(node.kind);
    const metrics = compact ? (node.metrics ?? []).slice(0, 2) : (node.metrics ?? []);
    const chips = compact ? (node.chips ?? []).slice(0, 3) : (node.chips ?? []);

    return (
      <article
        key={node.id}
        ref={(element) => {
          if (element) {
            nodeRefs.current.set(node.id, element);
          } else {
            nodeRefs.current.delete(node.id);
          }
        }}
        className={[
          "flow-node",
          visual.accentClass,
          statusClass(node.status),
          compact ? "flow-node--compact" : "",
        ].join(" ")}
        style={{ "--flow-node-max-width": `${node.preferredWidth ?? (compact ? 236 : 296)}px` } as CSSProperties}
      >
        <div className="flow-node__header">
          <span className="flow-node__icon" aria-hidden="true">
            <visual.Icon size={16} strokeWidth={2.2} />
          </span>
          <div className="flow-node__title-group">
            <strong>{node.title}</strong>
            {node.subtitle ? <div className="flow-node__subtitle">{node.subtitle}</div> : null}
          </div>
          {node.badge ? <span className="flow-node__badge">{node.badge}</span> : null}
        </div>

        {laneLabel ? <div className="flow-node__zone-tag">{laneLabel}</div> : null}

        {metrics.length ? (
          <div
            className={[
              "flow-node__metrics",
              node.singleRowMetrics ? "flow-node__metrics--single-row" : "",
            ].join(" ")}
          >
            {metrics.map((metric) => (
              <div key={`${node.id}-${metric.label}`} className="flow-node__metric">
                <span>{metric.label}</span>
                <strong>{metric.value}</strong>
              </div>
            ))}
          </div>
        ) : null}

        {chips.length ? (
          <div className="flow-node__chips">
            {chips.map((chip) => (
              <span key={`${node.id}-${chip}`} className="flow-node__chip">
                {chip}
              </span>
            ))}
          </div>
        ) : null}

        {!compact && node.preview ? <div className="flow-node__preview">{node.preview}</div> : null}
      </article>
    );
  };

  return (
    <div
      ref={containerRef}
      className={["flow-topology", compact ? "flow-topology--compact" : "", className].filter(Boolean).join(" ")}
    >
      <svg className="flow-topology__edges" aria-hidden="true">
        {graph.edges.map((edge) => {
          const from = bounds[edge.from];
          const to = bounds[edge.to];
          if (!from || !to) return null;

          const path = buildEdgePath(from, to);
          const labelX = from.right + (to.left - from.right) * 0.5;
          const labelY = from.centerY + (to.centerY - from.centerY) * 0.5;
          const labelWidth = compact ? 112 : 144;
          const strokeWidth = edgeStrokeWidth(edge.volume, maxEdgeVolume);

          return (
            <g key={edge.id} className={`flow-topology__edge ${statusClass(edge.status)}`}>
              <path className="flow-topology__edge-path" d={path} strokeWidth={strokeWidth} />
              {edge.active ? (
                <path className="flow-topology__edge-path flow-topology__edge-path--active" d={path} strokeWidth={strokeWidth + 1} />
              ) : null}
              <g className="flow-topology__edge-label" transform={`translate(${labelX - labelWidth / 2} ${labelY - 16})`}>
                <rect width={labelWidth} height={compact ? 28 : 34} rx="10" />
                <text x={10} y={14}>
                  {edge.label}
                </text>
                {!compact && edge.detail ? (
                  <text x={10} y={26} className="flow-topology__edge-detail">
                    {edge.detail}
                  </text>
                ) : null}
              </g>
            </g>
          );
        })}
      </svg>

      <div
        className="flow-topology__lanes"
        style={{ "--flow-topology-columns": `${Math.max(laneGroups.length, 1)}` } as CSSProperties}
      >
        {laneGroups.map((group) =>
          isZoneGroup(group) ? (
            <section key={group.id} className="flow-topology__zone">
              <div className="flow-topology__zone-header">
                <div className="flow-topology__zone-title">Capabilities Boundary</div>
                <div className="flow-topology__zone-tags">
                  {group.lanes.map((lane) => (
                    <span key={lane.key} className="flow-topology__zone-chip">
                      {lane.label}
                    </span>
                  ))}
                </div>
              </div>
              <div className="flow-topology__zone-grid">
                {group.lanes.flatMap((lane) => lane.nodes.map((node) => renderNode(node, lane.label)))}
              </div>
            </section>
          ) : (
            <div key={group.id} className="flow-topology__column">
              {group.lanes.map((lane) => (
                <div
                  key={lane.lane}
                  className={[
                    "flow-topology__lane",
                    `flow-topology__lane--${lane.key}`,
                    group.lanes.length > 1 ? "flow-topology__lane--stacked" : "",
                  ].join(" ")}
                >
                  <div className="flow-topology__lane-label">{lane.label}</div>
                  <div className="flow-topology__lane-stack">
                    {lane.nodes.map((node) => renderNode(node))}
                  </div>
                </div>
              ))}
            </div>
          ),
        )}
      </div>
    </div>
  );
}
