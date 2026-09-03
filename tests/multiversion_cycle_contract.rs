#![forbid(unsafe_code)]

use std::collections::{BTreeMap, BTreeSet};
use std::fmt::{Display, Formatter};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
struct NodeId {
    registry: &'static str,
    org: &'static str,
    name: &'static str,
    version: &'static str,
}

impl NodeId {
    const fn new(
        registry: &'static str,
        org: &'static str,
        name: &'static str,
        version: &'static str,
    ) -> Self {
        Self {
            registry,
            org,
            name,
            version,
        }
    }

    fn coordinate(self) -> String {
        format!("{}/{}", self.org, self.name)
    }

    fn storage_slug(self) -> String {
        format!(
            "{}__{}__{}__{}",
            self.registry, self.org, self.name, self.version
        )
    }
}

impl Display for NodeId {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        write!(
            formatter,
            "{}::{}/{}@{}",
            self.registry, self.org, self.name, self.version
        )
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct Edge {
    from: NodeId,
    to: NodeId,
    requirement: &'static str,
}

#[derive(Clone, Debug)]
struct ExactGraph {
    root: NodeId,
    nodes: BTreeMap<NodeId, &'static str>,
    edges: Vec<Edge>,
}

const A1: NodeId = NodeId::new("fixture-registry", "fixture", "a", "1");
const B1: NodeId = NodeId::new("fixture-registry", "fixture", "b", "1");
const A2: NodeId = NodeId::new("fixture-registry", "fixture", "a", "2");
const B0: NodeId = NodeId::new("fixture-registry", "fixture", "b", "0");

fn exact_cycle_graph() -> ExactGraph {
    ExactGraph {
        root: A1,
        nodes: BTreeMap::from([
            (A1, "sha256:01"),
            (B1, "sha256:02"),
            (A2, "sha256:03"),
            (B0, "sha256:04"),
        ]),
        edges: vec![
            Edge {
                from: A1,
                to: B1,
                requirement: "=1",
            },
            Edge {
                from: B1,
                to: A2,
                requirement: "=2",
            },
            Edge {
                from: A2,
                to: B0,
                requirement: "=0",
            },
            Edge {
                from: B0,
                to: A2,
                requirement: "=2",
            },
        ],
    }
}

fn validate(graph: &ExactGraph) -> Result<(), String> {
    if !graph.nodes.contains_key(&graph.root) {
        return Err(format!("missing root node {}", graph.root));
    }
    for edge in &graph.edges {
        if !graph.nodes.contains_key(&edge.from) {
            return Err(format!("edge source {} is absent", edge.from));
        }
        if !graph.nodes.contains_key(&edge.to) {
            return Err(format!("edge target {} is absent", edge.to));
        }
        if edge.requirement.trim().is_empty() {
            return Err(format!("edge {} -> {} has no requirement", edge.from, edge.to));
        }
    }
    Ok(())
}

fn outgoing(graph: &ExactGraph, node: NodeId) -> impl Iterator<Item = Edge> + '_ {
    graph
        .edges
        .iter()
        .copied()
        .filter(move |edge| edge.from == node)
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct BackEdge {
    cycle: Vec<NodeId>,
    closing: Edge,
}

fn traverse(graph: &ExactGraph) -> Result<(Vec<NodeId>, Vec<BackEdge>), String> {
    validate(graph)?;
    let mut visited = BTreeSet::new();
    let mut active = BTreeSet::new();
    let mut stack = Vec::new();
    let mut order = Vec::new();
    let mut back_edges = Vec::new();

    fn visit(
        graph: &ExactGraph,
        node: NodeId,
        visited: &mut BTreeSet<NodeId>,
        active: &mut BTreeSet<NodeId>,
        stack: &mut Vec<NodeId>,
        order: &mut Vec<NodeId>,
        back_edges: &mut Vec<BackEdge>,
    ) {
        if visited.contains(&node) {
            return;
        }
        active.insert(node);
        stack.push(node);
        order.push(node);

        for edge in outgoing(graph, node) {
            if active.contains(&edge.to) {
                let start = stack
                    .iter()
                    .position(|candidate| *candidate == edge.to)
                    .expect("active node must occur in the DFS stack");
                let mut cycle = stack[start..].to_vec();
                cycle.push(edge.to);
                back_edges.push(BackEdge {
                    cycle,
                    closing: edge,
                });
            } else if !visited.contains(&edge.to) {
                visit(
                    graph, edge.to, visited, active, stack, order, back_edges,
                );
            }
        }

        let popped = stack.pop();
        assert_eq!(popped, Some(node));
        active.remove(&node);
        visited.insert(node);
    }

    visit(
        graph,
        graph.root,
        &mut visited,
        &mut active,
        &mut stack,
        &mut order,
        &mut back_edges,
    );

    if visited.len() != graph.nodes.len() {
        return Err(format!(
            "root traversal reached {} of {} exact nodes",
            visited.len(),
            graph.nodes.len()
        ));
    }
    Ok((order, back_edges))
}

fn cycle_diagnostic(back_edge: &BackEdge) -> String {
    let path = back_edge
        .cycle
        .iter()
        .map(ToString::to_string)
        .collect::<Vec<_>>()
        .join(" -> ");
    format!(
        "dependency cycle detected: {path}; closing edge {} -> {} requires `{}`; \
         strategy=canonical-store-symlink; recursive-copy=stopped",
        back_edge.closing.from, back_edge.closing.to, back_edge.closing.requirement
    )
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct LinkSpec {
    link: PathBuf,
    target: PathBuf,
}

fn materialization_plan(root: &Path, graph: &ExactGraph) -> Result<Vec<LinkSpec>, String> {
    let (_, back_edges) = traverse(graph)?;
    let mut links = Vec::new();
    let node_root = |node: NodeId| root.join("nodes").join(node.storage_slug()).join("root");

    links.push(LinkSpec {
        link: root
            .join("project")
            .join("zed_modules")
            .join(graph.root.org)
            .join(graph.root.name),
        target: node_root(graph.root),
    });

    for edge in &graph.edges {
        links.push(LinkSpec {
            link: node_root(edge.from)
                .join("zed_modules")
                .join(edge.to.org)
                .join(edge.to.name),
            target: node_root(edge.to),
        });
    }

    let unique_links = links
        .iter()
        .map(|item| item.link.clone())
        .collect::<BTreeSet<_>>();
    if unique_links.len() != links.len() {
        return Err("materialization plan contains duplicate link paths".into());
    }
    if back_edges.len() != 1 {
        return Err(format!(
            "fixture must contain exactly one back-edge, found {}",
            back_edges.len()
        ));
    }
    Ok(links)
}

#[test]
fn exact_identity_keeps_multiple_versions_of_one_coordinate_distinct() {
    let graph = exact_cycle_graph();
    assert_eq!(A1.coordinate(), A2.coordinate());
    assert_ne!(A1, A2);
    assert_eq!(B1.coordinate(), B0.coordinate());
    assert_ne!(B1, B0);
    assert_eq!(graph.nodes.len(), 4);
}

#[test]
fn traversal_terminates_after_four_exact_nodes_and_one_back_edge() {
    let graph = exact_cycle_graph();
    let (order, back_edges) = traverse(&graph).unwrap();

    assert_eq!(order, [A1, B1, A2, B0]);
    assert_eq!(back_edges.len(), 1);
    assert_eq!(back_edges[0].cycle, [A2, B0, A2]);
    assert_eq!(back_edges[0].closing.from, B0);
    assert_eq!(back_edges[0].closing.to, A2);
}

#[test]
fn cycle_diagnostic_is_version_qualified_and_deterministic() {
    let graph = exact_cycle_graph();
    let (_, first) = traverse(&graph).unwrap();
    let (_, second) = traverse(&graph).unwrap();

    let first = cycle_diagnostic(&first[0]);
    let second = cycle_diagnostic(&second[0]);
    assert_eq!(first, second);
    assert!(first.contains("fixture-registry::fixture/a@2"));
    assert!(first.contains("fixture-registry::fixture/b@0"));
    assert!(first.contains("strategy=canonical-store-symlink"));
    assert!(first.contains("recursive-copy=stopped"));
}

#[test]
fn finite_plan_contains_one_root_link_and_one_link_per_edge() {
    let graph = exact_cycle_graph();
    let root = Path::new("/tmp/zed-cycle-contract");
    let plan = materialization_plan(root, &graph).unwrap();

    assert_eq!(plan.len(), graph.edges.len() + 1);
    let closing = plan
        .iter()
        .find(|item| {
            item.link.ends_with("nodes/fixture-registry__fixture__b__0/root/zed_modules/fixture/a")
        })
        .unwrap();
    assert!(closing
        .target
        .ends_with("nodes/fixture-registry__fixture__a__2/root"));
    assert!(plan.iter().all(|item| !item.target.starts_with(&item.link)));
}

#[test]
fn malformed_graph_fails_before_any_materialization_plan_is_created() {
    let mut graph = exact_cycle_graph();
    graph.nodes.remove(&A2);
    let error = materialization_plan(Path::new("/tmp/invalid"), &graph).unwrap_err();
    assert!(error.contains("edge target fixture-registry::fixture/a@2 is absent"));
}

#[cfg(unix)]
mod unix_symlink_tests {
    use super::*;
    use std::fs;
    use std::os::unix::fs::symlink;

    static NEXT_TEMP: AtomicU64 = AtomicU64::new(1);

    struct TempDir(PathBuf);

    impl TempDir {
        fn new() -> Self {
            let serial = NEXT_TEMP.fetch_add(1, Ordering::Relaxed);
            let path = std::env::temp_dir().join(format!(
                "zed-multiversion-cycle-{}-{serial}",
                std::process::id()
            ));
            let _ = fs::remove_dir_all(&path);
            fs::create_dir_all(&path).unwrap();
            Self(path)
        }
    }

    impl Drop for TempDir {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn apply_plan(root: &Path, graph: &ExactGraph) -> Vec<LinkSpec> {
        let plan = materialization_plan(root, graph).unwrap();
        for node in graph.nodes.keys().copied() {
            let node_root = root.join("nodes").join(node.storage_slug()).join("root");
            fs::create_dir_all(&node_root).unwrap();
            fs::write(node_root.join("identity.txt"), format!("{node}\n")).unwrap();
        }
        for spec in &plan {
            fs::create_dir_all(spec.link.parent().unwrap()).unwrap();
            if fs::symlink_metadata(&spec.link).is_ok() {
                fs::remove_file(&spec.link).unwrap();
            }
            symlink(fs::canonicalize(&spec.target).unwrap(), &spec.link).unwrap();
        }
        plan
    }

    #[test]
    fn real_symlinks_close_the_cycle_without_recursive_directory_copies() {
        let temp = TempDir::new();
        let graph = exact_cycle_graph();
        let plan = apply_plan(&temp.0, &graph);

        let node_dirs = fs::read_dir(temp.0.join("nodes"))
            .unwrap()
            .collect::<Result<Vec<_>, _>>()
            .unwrap();
        assert_eq!(node_dirs.len(), 4);
        assert_eq!(plan.len(), 5);
        assert!(plan.iter().all(|spec| {
            fs::symlink_metadata(&spec.link)
                .unwrap()
                .file_type()
                .is_symlink()
        }));

        let closing_link = temp
            .0
            .join("nodes/fixture-registry__fixture__b__0/root/zed_modules/fixture/a");
        let canonical_a2 = fs::canonicalize(
            temp.0
                .join("nodes/fixture-registry__fixture__a__2/root"),
        )
        .unwrap();
        assert_eq!(fs::read_link(closing_link).unwrap(), canonical_a2);
    }

    #[test]
    fn applying_the_same_plan_twice_is_idempotent() {
        let temp = TempDir::new();
        let graph = exact_cycle_graph();
        let first = apply_plan(&temp.0, &graph);
        let first_targets = first
            .iter()
            .map(|spec| (spec.link.clone(), fs::read_link(&spec.link).unwrap()))
            .collect::<BTreeMap<_, _>>();

        let second = apply_plan(&temp.0, &graph);
        let second_targets = second
            .iter()
            .map(|spec| (spec.link.clone(), fs::read_link(&spec.link).unwrap()))
            .collect::<BTreeMap<_, _>>();

        assert_eq!(first, second);
        assert_eq!(first_targets, second_targets);
        assert_eq!(
            fs::read_dir(temp.0.join("nodes"))
                .unwrap()
                .collect::<Result<Vec<_>, _>>()
                .unwrap()
                .len(),
            4
        );
    }
}
