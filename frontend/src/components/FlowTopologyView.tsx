import type { CSSProperties } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Bot, Boxes, BrainCircuit, Cpu, Crown, Globe, Monitor, Server, UserRound, Wrench } from "lucide-react";
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
  minWidthOverride?: number;
  maxWidthOverride?: number;
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

type EdgeLabelPlacement = {
  left: number;
  top: number;
  width: number;
  height: number;
};

type FlowTopologyViewport = {
  width: number;
  height: number;
};

type EdgeCurveGeometry = {
  startX: number;
  startY: number;
  control1X: number;
  control1Y: number;
  control2X: number;
  control2Y: number;
  endX: number;
  endY: number;
};

type ZoneLayoutPlan = {
  rows: FlowTopologyNode[][];
};

type EdgeAnchorPair = {
  orientation: "horizontal" | "vertical";
  startX: number;
  startY: number;
  endX: number;
  endY: number;
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
      return { Icon: Cpu, accentClass: "flow-node--runtime" };
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

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function edgeStrokeWidth(volume: number, maxVolume: number) {
  if (maxVolume <= 0) return 2;
  const ratio = Math.max(0, Math.min(volume / maxVolume, 1));
  return 2 + ratio * 4;
}

function clampWithinNode(value: number, min: number, max: number, inset = 16) {
  return clamp(value, min + inset, max - inset);
}

function anchorDistance(anchor: EdgeAnchorPair) {
  return Math.hypot(anchor.endX - anchor.startX, anchor.endY - anchor.startY);
}

function buildEdgeCurve(from: NodeBounds, to: NodeBounds): EdgeCurveGeometry {
  const rightGap = to.left - from.right;
  const leftGap = from.left - to.right;
  const downGap = to.top - from.bottom;
  const upGap = from.top - to.bottom;
  const preferredAxis =
    Math.max(downGap, upGap) > Math.max(rightGap, leftGap) + 24
      ? "vertical"
      : Math.max(rightGap, leftGap) > Math.max(downGap, upGap) + 24
        ? "horizontal"
        : null;
  const anchors: EdgeAnchorPair[] = [
    {
      orientation: "horizontal",
      startX: from.right,
      startY: clampWithinNode(to.centerY, from.top, from.bottom),
      endX: to.left,
      endY: clampWithinNode(from.centerY, to.top, to.bottom),
    },
    {
      orientation: "horizontal",
      startX: from.left,
      startY: clampWithinNode(to.centerY, from.top, from.bottom),
      endX: to.right,
      endY: clampWithinNode(from.centerY, to.top, to.bottom),
    },
    {
      orientation: "vertical",
      startX: clampWithinNode(to.centerX, from.left, from.right),
      startY: from.bottom,
      endX: clampWithinNode(from.centerX, to.left, to.right),
      endY: to.top,
    },
    {
      orientation: "vertical",
      startX: clampWithinNode(to.centerX, from.left, from.right),
      startY: from.top,
      endX: clampWithinNode(from.centerX, to.left, to.right),
      endY: to.bottom,
    },
  ];

  const candidateAnchors = preferredAxis
    ? anchors.filter((anchor) => anchor.orientation === preferredAxis)
    : anchors;
  const anchor = candidateAnchors.reduce((best, current) =>
    anchorDistance(current) < anchorDistance(best) ? current : best,
  );
  const { startX, startY, endX, endY } = anchor;
  const distance =
    anchor.orientation === "horizontal"
      ? Math.max(Math.abs(endX - startX), 80)
      : Math.max(Math.abs(endY - startY), 80);
  const control = Math.max(56, distance * 0.38);

  if (anchor.orientation === "vertical") {
    return {
      startX,
      startY,
      control1X: startX,
      control1Y: startY + (endY >= startY ? control : -control),
      control2X: endX,
      control2Y: endY - (endY >= startY ? control : -control),
      endX,
      endY,
    };
  }

  return {
    startX,
    startY,
    control1X: startX + control,
    control1Y: startY,
    control2X: endX - control,
    control2Y: endY,
    endX,
    endY,
  };
}

function buildEdgePath(curve: EdgeCurveGeometry) {
  return `M ${curve.startX} ${curve.startY} C ${curve.control1X} ${curve.control1Y}, ${curve.control2X} ${curve.control2Y}, ${curve.endX} ${curve.endY}`;
}

function cubicBezierPoint(curve: EdgeCurveGeometry, t: number) {
  const mt = 1 - t;
  const x =
    mt * mt * mt * curve.startX +
    3 * mt * mt * t * curve.control1X +
    3 * mt * t * t * curve.control2X +
    t * t * t * curve.endX;
  const y =
    mt * mt * mt * curve.startY +
    3 * mt * mt * t * curve.control1Y +
    3 * mt * t * t * curve.control2Y +
    t * t * t * curve.endY;
  return { x, y };
}

function cubicBezierTangent(curve: EdgeCurveGeometry, t: number) {
  const mt = 1 - t;
  const x =
    3 * mt * mt * (curve.control1X - curve.startX) +
    6 * mt * t * (curve.control2X - curve.control1X) +
    3 * t * t * (curve.endX - curve.control2X);
  const y =
    3 * mt * mt * (curve.control1Y - curve.startY) +
    6 * mt * t * (curve.control2Y - curve.control1Y) +
    3 * t * t * (curve.endY - curve.control2Y);
  return { x, y };
}

function normalizeVector(x: number, y: number) {
  const length = Math.hypot(x, y);
  if (length <= 0.0001) {
    return { x: 1, y: 0 };
  }
  return { x: x / length, y: y / length };
}

function rectIntersectsNode(
  rect: { left: number; top: number; width: number; height: number },
  node: NodeBounds,
  margin = 10,
) {
  const right = rect.left + rect.width;
  const bottom = rect.top + rect.height;
  return !(
    right < node.left - margin ||
    rect.left > node.right + margin ||
    bottom < node.top - margin ||
    rect.top > node.bottom + margin
  );
}

function rectIntersectsRect(
  left: { left: number; top: number; width: number; height: number },
  right: { left: number; top: number; width: number; height: number },
  margin = 8,
) {
  return !(
    left.left + left.width < right.left - margin ||
    left.left > right.left + right.width + margin ||
    left.top + left.height < right.top - margin ||
    left.top > right.top + right.height + margin
  );
}

function placementBlocked(
  placement: EdgeLabelPlacement,
  bounds: Record<string, NodeBounds>,
  occupied: EdgeLabelPlacement[],
) {
  return (
    Object.values(bounds).some((node) => rectIntersectsNode(placement, node)) ||
    occupied.some((label) => rectIntersectsRect(placement, label))
  );
}

function clampPlacementToViewport(
  placement: EdgeLabelPlacement,
  viewport: FlowTopologyViewport,
  margin = 8,
): EdgeLabelPlacement {
  const maxLeft = Math.max(margin, viewport.width - placement.width - margin);
  const maxTop = Math.max(margin, viewport.height - placement.height - margin);
  return {
    ...placement,
    left: clamp(placement.left, margin, maxLeft),
    top: clamp(placement.top, margin, maxTop),
  };
}

function estimateEdgeLabelSize(edge: FlowTopologyEdge, compact: boolean) {
  const mainWidth = 20 + edge.label.length * (compact ? 6.1 : 6.6);
  const detailWidth =
    compact || !edge.detail ? 0 : 20 + Math.min(edge.detail.length, 36) * 5.2;
  return {
    width: clamp(
      Math.round(Math.max(mainWidth, detailWidth)),
      compact ? 88 : 104,
      compact ? 180 : 260,
    ),
    height: compact || !edge.detail ? 28 : 34,
  };
}

function resolveEdgeLabelPlacement(
  edge: FlowTopologyEdge,
  from: NodeBounds,
  to: NodeBounds,
  bounds: Record<string, NodeBounds>,
  compact: boolean,
  occupied: EdgeLabelPlacement[],
  viewport: FlowTopologyViewport,
): EdgeLabelPlacement {
  const { width, height } = estimateEdgeLabelSize(edge, compact);
  const curve = buildEdgeCurve(from, to);
  const tSamples = compact ? [0.42, 0.58, 0.32, 0.68, 0.5] : [0.38, 0.5, 0.62, 0.28, 0.72];
  const normalOffsets = compact ? [-14, 14, -24, 24, 0] : [-18, 18, -30, 30, 0];
  const tangentOffsets = compact ? [0, -14, 14, -26, 26] : [0, -18, 18, -32, 32];

  for (const t of tSamples) {
    const point = cubicBezierPoint(curve, t);
    const tangent = cubicBezierTangent(curve, t);
    const unitTangent = normalizeVector(tangent.x, tangent.y);
    const unitNormal = { x: -unitTangent.y, y: unitTangent.x };

    for (const normalOffset of normalOffsets) {
      for (const tangentOffset of tangentOffsets) {
        const centerX = point.x + unitNormal.x * normalOffset + unitTangent.x * tangentOffset;
        const centerY = point.y + unitNormal.y * normalOffset + unitTangent.y * tangentOffset;
        const placement = clampPlacementToViewport(
          {
            left: centerX - width / 2,
            top: centerY - height / 2,
            width,
            height,
          },
          viewport,
        );
        if (!placementBlocked(placement, bounds, occupied)) {
          return placement;
        }
      }
    }
  }

  const fallbackPoint = cubicBezierPoint(curve, 0.5);
  return clampPlacementToViewport(
    {
      left: fallbackPoint.x - width / 2,
      top: fallbackPoint.y - height / 2 - (compact ? 14 : 18),
      width,
      height,
    },
    viewport,
  );
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
  if (!compact) {
    return lanes.map((lane) => ({
      id: lane.key,
      lanes: [lane],
      preferredWidth: lane.preferredWidth,
    }));
  }

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

function zoneGroupKind(group: FlowLaneGroup) {
  const keys = new Set(group.lanes.map((lane) => lane.key));
  if (keys.size === 1 && keys.has("outside")) return "outside";
  if (keys.size === 1 && keys.has("capabilities")) return "capabilities";
  return "mixed";
}

function zoneTitleForGroup(group: FlowLaneGroup) {
  const kind = zoneGroupKind(group);
  if (kind === "outside") return "Outside Boundary";
  if (kind === "capabilities") return "Capabilities Boundary";
  return "Capabilities + Outside";
}

function zoneRowContentWidth(group: FlowLaneGroup, compact: boolean) {
  const cardGap = compact ? 12 : 24;
  const nodeWidths = group.lanes.flatMap((lane) => lane.nodes.map((node) => node.preferredWidth ?? (compact ? 236 : 296)));
  return nodeWidths.reduce((total, width) => total + width, 0) + Math.max(0, nodeWidths.length - 1) * cardGap;
}

function zoneHeaderWidth(group: FlowLaneGroup, compact: boolean) {
  const titleWidth = 28 + zoneTitleForGroup(group).length * (compact ? 6 : 7);
  const tagsWidth =
    group.lanes.reduce((total, lane) => total + 18 + lane.label.length * (compact ? 5 : 6), 0) +
    Math.max(0, group.lanes.length - 1) * 6;
  return Math.max(titleWidth, tagsWidth);
}

function displayGroupOrder(group: FlowLaneGroup) {
  const key = group.lanes[0]?.key ?? "";
  if (key === "entry") return 0;
  if (key === "client") return 1;
  if (key === "platform") return 2;
  if (key === "runtime") return 3;
  if (key === "llm") return 4;
  if (key === "outside") return 5;
  if (key === "capabilities") return 6;
  return 99;
}

function groupLayoutClasses(group: FlowLaneGroup) {
  return group.lanes.map((lane) => `flow-topology__group--${lane.key}`).join(" ");
}

function measureNodeWidth(node: FlowTopologyNode, compact: boolean) {
  return node.preferredWidth ?? (compact ? 236 : 296);
}

function buildZoneLayoutPlan(group: FlowLaneGroup, compact: boolean, availableWidth: number): ZoneLayoutPlan {
  const nodes = group.lanes.flatMap((lane) => lane.nodes);
  if (compact || nodes.length <= 1) {
    return { rows: [nodes] };
  }

  const gap = 24;
  const widths = nodes.map((node) => measureNodeWidth(node, compact));
  const totalWidth = widths.reduce((sum, width) => sum + width, 0) + Math.max(0, widths.length - 1) * gap;
  if (totalWidth <= availableWidth) {
    return { rows: [nodes] };
  }

  let bestPlan: ZoneLayoutPlan | null = null;
  let bestWaste = Number.POSITIVE_INFINITY;
  let bestScore = Number.POSITIVE_INFINITY;

  for (let splitIndex = 1; splitIndex < nodes.length; splitIndex += 1) {
    const topNodes = nodes.slice(0, splitIndex);
    const bottomNodes = nodes.slice(splitIndex);
    const topWidth =
      widths.slice(0, splitIndex).reduce((sum, width) => sum + width, 0) + Math.max(0, topNodes.length - 1) * gap;
    const bottomWidth =
      widths.slice(splitIndex).reduce((sum, width) => sum + width, 0) + Math.max(0, bottomNodes.length - 1) * gap;
    const overflow = Math.max(0, topWidth - availableWidth) + Math.max(0, bottomWidth - availableWidth);
    const waste = Math.abs(topWidth - bottomWidth);
    const score = overflow * 1000 + waste;

    if (score < bestScore || (score === bestScore && waste < bestWaste)) {
      bestScore = score;
      bestWaste = waste;
      bestPlan = { rows: [topNodes, bottomNodes] };
    }
  }

  return bestPlan ?? { rows: [nodes] };
}

function zoneContentWidth(group: FlowLaneGroup, compact: boolean, labelChannelWidth: number) {
  const kind = zoneGroupKind(group);
  const horizontalPadding = compact ? 28 : 36;
  const effectiveLabelChannel = kind === "outside" ? (compact ? 20 : 36) : labelChannelWidth;
  const headerReserve = zoneHeaderWidth(group, compact);
  const rowWidth = zoneRowContentWidth(group, compact);
  return horizontalPadding + effectiveLabelChannel + Math.max(headerReserve, rowWidth);
}

export function FlowTopologyView({ graph, compact = false, className }: FlowTopologyViewProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const nodeRefs = useRef(new Map<string, HTMLElement>());
  const [bounds, setBounds] = useState<Record<string, NodeBounds>>({});
  const [containerWidth, setContainerWidth] = useState(0);
  const [viewport, setViewport] = useState<FlowTopologyViewport>({ width: 0, height: 0 });

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

  const displayLaneGroups = useMemo(
    () => [...laneGroups].sort((left, right) => displayGroupOrder(left) - displayGroupOrder(right)),
    [laneGroups],
  );

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    let frameId = 0;
    const measure = () => {
      frameId = 0;
      const rootRect = container.getBoundingClientRect();
      setContainerWidth(rootRect.width);
      setViewport({
        width: rootRect.width,
        height: rootRect.height,
      });
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

  const maxEdgeLabelWidth = useMemo(
    () =>
      graph.edges.reduce(
        (maxWidth, edge) => Math.max(maxWidth, estimateEdgeLabelSize(edge, compact).width),
        compact ? 112 : 148,
      ),
    [compact, graph.edges],
  );

  const labelChannelWidth = useMemo(
    () => Math.max(compact ? 40 : 96, maxEdgeLabelWidth + (compact ? 18 : 34)),
    [compact, maxEdgeLabelWidth],
  );

  const interGroupGap = useMemo(
    () => Math.max(compact ? 16 : 44, Math.round(maxEdgeLabelWidth * 0.7)),
    [compact, maxEdgeLabelWidth],
  );

  const topologyStyle = useMemo(
    () =>
      ({
        "--flow-topology-label-channel": `${labelChannelWidth}px`,
        "--flow-topology-inter-group-gap": `${interGroupGap}px`,
      }) as CSSProperties,
    [interGroupGap, labelChannelWidth],
  );

  const edgeLabelPlacements = useMemo(
    () => {
      const occupied: EdgeLabelPlacement[] = [];
      return graph.edges
        .map((edge) => {
          const from = bounds[edge.from];
          const to = bounds[edge.to];
          if (!from || !to) return null;
          const placement = resolveEdgeLabelPlacement(edge, from, to, bounds, compact, occupied, viewport);
          occupied.push(placement);
          return { edge, placement };
        })
        .filter((item): item is { edge: FlowTopologyEdge; placement: EdgeLabelPlacement } => Boolean(item));
    },
    [bounds, compact, graph.edges, viewport],
  );

  const edgeLayerStyle = useMemo(
    () =>
      viewport.width > 0 && viewport.height > 0
        ? ({
            width: `${viewport.width}px`,
            height: `${viewport.height}px`,
          } as CSSProperties)
        : undefined,
    [viewport],
  );

  const isZoneGroup = (group: FlowLaneGroup) =>
    group.lanes.some((lane) => lane.key === "capabilities" || lane.key === "outside");

  const zoneGroupStyle = (group: FlowLaneGroup): CSSProperties | undefined => {
    if (compact) return undefined;
    const kind = zoneGroupKind(group);
    const rowWidth = zoneRowContentWidth(group, compact);
    const headerWidth = zoneHeaderWidth(group, compact);
    const minWidth =
      kind === "outside"
        ? clamp(Math.max(rowWidth, headerWidth) + 36, 220, 620)
        : clamp(Math.max(rowWidth * 0.72, headerWidth) + labelChannelWidth + 36, 360, 960);
    const maxWidth = Math.max(minWidth, containerWidth - 8);
    const contentWidth = zoneContentWidth(group, compact, labelChannelWidth);
    const resolvedWidth = clamp(contentWidth, minWidth, maxWidth);
    return {
      "--flow-zone-min-width": `${minWidth}px`,
      "--flow-zone-width": `${resolvedWidth}px`,
      "--flow-zone-label-channel": `${kind === "outside" ? 0 : labelChannelWidth}px`,
      width: "100%",
      minWidth: `min(100%, ${minWidth}px)`,
      flexGrow: 0,
      flexShrink: 1,
    } as CSSProperties;
  };

  const zoneLayoutPlan = (group: FlowLaneGroup): ZoneLayoutPlan => {
    if (compact) {
      return { rows: [group.lanes.flatMap((lane) => lane.nodes)] };
    }
    const kind = zoneGroupKind(group);
    const availableWidth =
      kind === "capabilities"
        ? Math.max(360, containerWidth * 0.6)
        : kind === "outside"
          ? Math.max(220, containerWidth * 0.28)
          : containerWidth;
    return buildZoneLayoutPlan(group, compact, availableWidth);
  };

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
      style={topologyStyle}
    >
      <svg className="flow-topology__edges" aria-hidden="true" style={edgeLayerStyle}>
        {graph.edges.map((edge) => {
          const from = bounds[edge.from];
          const to = bounds[edge.to];
          if (!from || !to) return null;

          const path = buildEdgePath(buildEdgeCurve(from, to));
          const strokeWidth = edgeStrokeWidth(edge.volume, maxEdgeVolume);

          return (
            <g key={edge.id} className={`flow-topology__edge ${statusClass(edge.status)}`}>
              <path className="flow-topology__edge-path" d={path} strokeWidth={strokeWidth} />
              {edge.active ? (
                <path className="flow-topology__edge-path flow-topology__edge-path--active" d={path} strokeWidth={strokeWidth + 1} />
              ) : null}
            </g>
          );
        })}
      </svg>

      <div className="flow-topology__edge-labels" aria-hidden="true" style={edgeLayerStyle}>
        {edgeLabelPlacements.map(({ edge, placement }) => (
          <div
            key={edge.id}
            className={`flow-topology__edge-label-badge ${statusClass(edge.status)}`}
            style={{
              left: `${placement.left}px`,
              top: `${placement.top}px`,
              width: `${placement.width}px`,
              minHeight: `${placement.height}px`,
            }}
          >
            <div className="flow-topology__edge-label-main">{edge.label}</div>
            {!compact && edge.detail ? <div className="flow-topology__edge-label-sub">{edge.detail}</div> : null}
          </div>
        ))}
      </div>

      <div
        className="flow-topology__lanes"
        style={{ "--flow-topology-columns": `${Math.max(laneGroups.length, 1)}` } as CSSProperties}
      >
        {displayLaneGroups.map((group) =>
          isZoneGroup(group) ? (
            <section
              key={group.id}
              className={[
                "flow-topology__group",
                groupLayoutClasses(group),
                "flow-topology__zone",
                `flow-topology__zone--${zoneGroupKind(group)}`,
              ].join(" ")}
              style={zoneGroupStyle(group)}
            >
              <div className="flow-topology__zone-header">
                <div className="flow-topology__zone-title">{zoneTitleForGroup(group)}</div>
                <div className="flow-topology__zone-tags">
                  {group.lanes.map((lane) => (
                    <span key={lane.key} className="flow-topology__zone-chip">
                      {lane.label}
                    </span>
                  ))}
                </div>
              </div>
              <div className="flow-topology__zone-grid">
                {zoneLayoutPlan(group).rows.map((rowNodes, rowIndex) => (
                  <div key={`${group.id}-row-${rowIndex}`} className="flow-topology__zone-row">
                    {rowNodes.map((node) => {
                      const lane = group.lanes.find((item) => item.nodes.some((candidate) => candidate.id === node.id));
                      return renderNode(node, lane?.label);
                    })}
                  </div>
                ))}
              </div>
            </section>
          ) : (
            <div key={group.id} className={["flow-topology__group", groupLayoutClasses(group), "flow-topology__column"].join(" ")}>
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
