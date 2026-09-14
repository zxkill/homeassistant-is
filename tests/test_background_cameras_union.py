def test_options_flow_unions_yard_cameras_and_unmatched_doors(component_root):
    source = (component_root / "options_flow.py").read_text()

    # A door without a matching yard camera (e.g. a shared/additional
    # intercom on a different address) must stay selectable even when the
    # account has yard cameras for other doors. This must not regress back
    # to an either/or "if yard_cameras: ... else: ..." branch that picks
    # only one source for the whole account.
    assert "matched_door_uids" in source
    assert "door.uid not in matched_door_uids" in source
    assert "choices.update(" in source


def test_background_processor_unions_yard_cameras_and_unmatched_doors(component_root):
    source = (component_root / "background.py").read_text()

    assert "matched_door_uids" in source
    assert "door.uid not in matched_door_uids" in source
    assert "available_uids = set(yard_available) | set(door_available)" in source


def test_camera_platform_unions_yard_cameras_and_unmatched_doors(component_root):
    source = (component_root / "camera.py").read_text()

    # Same bug class as options_flow.py/background.py: a door without a
    # matched yard camera must still get its own IntersvyazDoorCamera entity
    # even when the account has yard cameras for other doors, instead of the
    # whole account being pinned to a single "either yard or relay" source.
    assert "matched_door_uids" in source
    assert "door.uid not in matched_door_uids" in source
    assert "IntersvyazYardCamera(entry, camera) for camera in yard_cameras" in source
    assert "IntersvyazDoorCamera(entry, door) for door in unmatched_doors" in source
