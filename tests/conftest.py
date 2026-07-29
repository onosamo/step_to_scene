from pathlib import Path

import pytest


@pytest.fixture
def test_data_dir() -> Path:
    """Return the path to the tests/data directory."""
    return Path(__file__).parent / "data"


@pytest.fixture
def test_step_file(test_data_dir: Path) -> Path:
    """Return the path to the test STEP file."""
    return test_data_dir / "test_step.step"


@pytest.fixture(scope="session")
def assembly_step_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A small generated assembly STEP file covering the tricky cases:

    - two DISTINCT products that share the name "widget" (a box and a sphere)
    - the box "widget" placed twice (two instances of one product)
    - a sub-assembly "subasm" (pin + plate) placed twice
    """
    from OCP.BRep import BRep_Builder
    from OCP.BRepPrimAPI import (
        BRepPrimAPI_MakeBox,
        BRepPrimAPI_MakeCylinder,
        BRepPrimAPI_MakeSphere,
    )
    from OCP.gp import gp_Trsf, gp_Vec
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDF import TDF_Label
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS_Compound, TopoDS_Shape
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())

    def set_name(label: TDF_Label, name: str):
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(name))

    def location(x: float, y: float, z: float) -> TopLoc_Location:
        trsf = gp_Trsf()
        trsf.SetTranslation(gp_Vec(x, y, z))
        return TopLoc_Location(trsf)

    def empty_compound() -> TopoDS_Shape:
        builder = BRep_Builder()
        compound = TopoDS_Compound()
        builder.MakeCompound(compound)
        return compound

    widget_box = shape_tool.AddShape(
        BRepPrimAPI_MakeBox(10.0, 10.0, 10.0).Shape(), False
    )
    set_name(widget_box, "widget")
    widget_sphere = shape_tool.AddShape(BRepPrimAPI_MakeSphere(8.0).Shape(), False)
    set_name(widget_sphere, "widget")
    bracket = shape_tool.AddShape(BRepPrimAPI_MakeCylinder(5.0, 20.0).Shape(), False)
    set_name(bracket, "bracket")
    pin = shape_tool.AddShape(BRepPrimAPI_MakeCylinder(2.0, 15.0).Shape(), False)
    set_name(pin, "pin")
    plate = shape_tool.AddShape(BRepPrimAPI_MakeBox(30.0, 30.0, 3.0).Shape(), False)
    set_name(plate, "plate")

    subasm = shape_tool.AddShape(empty_compound(), True)
    set_name(subasm, "subasm")
    shape_tool.AddComponent(subasm, pin, location(0.0, 0.0, 3.0))
    shape_tool.AddComponent(subasm, plate, location(0.0, 0.0, 0.0))

    root = shape_tool.AddShape(empty_compound(), True)
    set_name(root, "cell")
    shape_tool.AddComponent(root, widget_box, location(0.0, 0.0, 0.0))
    shape_tool.AddComponent(root, widget_box, location(100.0, 0.0, 0.0))
    shape_tool.AddComponent(root, widget_sphere, location(200.0, 0.0, 0.0))
    shape_tool.AddComponent(root, bracket, location(0.0, 100.0, 0.0))
    shape_tool.AddComponent(root, subasm, location(0.0, 0.0, 100.0))
    shape_tool.AddComponent(root, subasm, location(0.0, 0.0, 200.0))

    shape_tool.UpdateAssemblies()

    out_path = tmp_path_factory.mktemp("step_fixtures") / "assembly.step"
    writer = STEPCAFControl_Writer()
    writer.Transfer(doc)
    status = writer.Write(str(out_path))
    assert status == 1, f"failed to write fixture STEP file: {status}"

    return out_path
