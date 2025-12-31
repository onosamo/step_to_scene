import re
from pathlib import Path
from xml.etree import ElementTree as ET


class CommentedTreeBuilder(ET.TreeBuilder):
    def comment(self, data):
        self.start(ET.Comment, {})
        self.data(data)
        self.end(ET.Comment)


XACRO_NS = {"xacro": "http://www.ros.org/wiki/xacro"}


def parse_xml_with_comments(path: Path) -> ET.ElementTree:
    parser = ET.XMLParser(target=CommentedTreeBuilder())
    return ET.parse(path, parser)


def parse_xml_safe(path: Path) -> ET.Element:
    try:
        tree = ET.parse(path)
        return tree.getroot()
    except ET.ParseError:
        with open(path, encoding="utf-8") as f:
            content = f.read()
        content = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
        return ET.fromstring(content)


def find_xacro_includes(root: ET.Element) -> list[ET.Element]:
    includes = root.findall(".//xacro:include", XACRO_NS)
    includes.extend(root.findall(".//include"))
    return includes


def get_mesh_info(root: ET.Element) -> tuple[str | None, list[float]]:
    mesh = root.find(".//collision/geometry/mesh")
    if mesh is None:
        mesh = root.find(".//visual/geometry/mesh")

    if mesh is None:
        return None, [1.0, 1.0, 1.0]

    mesh_filename = mesh.get("filename")
    scale_str = mesh.get("scale", "1 1 1")
    scale = [float(x) for x in scale_str.split()]

    return mesh_filename, scale


def parse_xacro_includes(
    xacro_path: Path,
) -> tuple[list[tuple[ET.Element, Path]], ET.ElementTree]:
    tree = parse_xml_with_comments(xacro_path)
    root = tree.getroot()

    included_files: list[tuple[ET.Element, Path]] = []
    for include in find_xacro_includes(root):
        filename = include.get("filename")
        if filename:
            urdf_path = xacro_path.parent / filename
            if urdf_path.exists():
                included_files.append((include, urdf_path))

    return included_files, tree


def parse_xacro_with_transforms(xacro_path: Path) -> tuple[list[Path], dict[str, dict]]:
    tree = ET.parse(xacro_path)
    root = tree.getroot()

    included_files: list[Path] = []
    for include in find_xacro_includes(root):
        filename = include.get("filename")
        if filename:
            urdf_path = xacro_path.parent / filename
            if urdf_path.exists():
                included_files.append(urdf_path)

    joint_transforms: dict[str, dict] = {}
    for joint in root.findall(".//joint"):
        joint_type = joint.get("type")
        if joint_type == "fixed":
            child = joint.find("child")
            if child is not None:
                child_link = child.get("link")
                if child_link is None:
                    continue

                origin = joint.find("origin")
                if origin is not None:
                    xyz_str = origin.get("xyz", "0 0 0")
                    rpy_str = origin.get("rpy", "0 0 0")

                    xyz = [float(x) for x in xyz_str.split()]
                    rpy = [float(x) for x in rpy_str.split()]

                    joint_transforms[child_link] = {"xyz": xyz, "rpy": rpy}

    return included_files, joint_transforms


def parse_urdf_mesh_info(
    urdf_path: Path,
) -> tuple[str | None, str | None, list[float] | None]:
    root = parse_xml_safe(urdf_path)

    link = root.find(".//link")
    if link is None:
        return None, None, None

    link_name = link.get("name")

    mesh_filename, scale = get_mesh_info(root)
    if mesh_filename is None:
        return None, link_name, None

    return mesh_filename, link_name, scale
