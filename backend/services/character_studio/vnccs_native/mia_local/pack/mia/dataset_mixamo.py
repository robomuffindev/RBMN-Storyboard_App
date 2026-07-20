"""Mixamo skeleton definitions used by MIA inference pipeline."""

from collections import OrderedDict
from dataclasses import dataclass
from functools import cached_property
from types import MappingProxyType
from typing import Iterator

try:
    from typing import Self
except ImportError:
    from typing_extensions import Self

MIXAMO_PREFIX = "mixamorig:"
MIXAMO_JOINTS = (
    "mixamorig:Hips",
    "mixamorig:Spine",
    "mixamorig:Spine1",
    "mixamorig:Spine2",
    "mixamorig:Neck",
    "mixamorig:Head",
    "mixamorig:LeftShoulder",
    "mixamorig:LeftArm",
    "mixamorig:LeftForeArm",
    "mixamorig:LeftHand",
    "mixamorig:LeftHandThumb1",
    "mixamorig:LeftHandThumb2",
    "mixamorig:LeftHandThumb3",
    "mixamorig:LeftHandIndex1",
    "mixamorig:LeftHandIndex2",
    "mixamorig:LeftHandIndex3",
    "mixamorig:LeftHandMiddle1",
    "mixamorig:LeftHandMiddle2",
    "mixamorig:LeftHandMiddle3",
    "mixamorig:LeftHandRing1",
    "mixamorig:LeftHandRing2",
    "mixamorig:LeftHandRing3",
    "mixamorig:LeftHandPinky1",
    "mixamorig:LeftHandPinky2",
    "mixamorig:LeftHandPinky3",
    "mixamorig:RightShoulder",
    "mixamorig:RightArm",
    "mixamorig:RightForeArm",
    "mixamorig:RightHand",
    "mixamorig:RightHandThumb1",
    "mixamorig:RightHandThumb2",
    "mixamorig:RightHandThumb3",
    "mixamorig:RightHandIndex1",
    "mixamorig:RightHandIndex2",
    "mixamorig:RightHandIndex3",
    "mixamorig:RightHandMiddle1",
    "mixamorig:RightHandMiddle2",
    "mixamorig:RightHandMiddle3",
    "mixamorig:RightHandRing1",
    "mixamorig:RightHandRing2",
    "mixamorig:RightHandRing3",
    "mixamorig:RightHandPinky1",
    "mixamorig:RightHandPinky2",
    "mixamorig:RightHandPinky3",
    "mixamorig:LeftUpLeg",
    "mixamorig:LeftLeg",
    "mixamorig:LeftFoot",
    "mixamorig:LeftToeBase",
    "mixamorig:RightUpLeg",
    "mixamorig:RightLeg",
    "mixamorig:RightFoot",
    "mixamorig:RightToeBase",
)
assert len(MIXAMO_JOINTS) == 52
JOINTS_NUM = len(MIXAMO_JOINTS)
BONES_IDX_DICT = MappingProxyType(OrderedDict({name: i for i, name in enumerate(MIXAMO_JOINTS)}))


@dataclass(frozen=True)
class Joint:
    name: str
    index: int
    parent: Self | None
    children: list[Self]
    template_joints: tuple[str]

    def __repr__(self):
        return f"{self.__class__.__name__}({self.name})"

    def __iter__(self) -> Iterator[Self]:
        yield self
        for child in self.children:
            yield from child

    @cached_property
    def children_recursive(self) -> list[Self]:
        children_list = []
        if not self.children:
            return children_list
        for child in self.children:
            children_list.append(child)
            children_list.extend(child.children_recursive)
        return children_list

    def __len__(self):
        return len(self.children_recursive) + 1

    def __contains__(self, item: Self | str):
        if isinstance(item, str):
            return item == self.name or item in (child.name for child in self.children_recursive)
        elif isinstance(item, Joint):
            return item is Self or item in self.children_recursive
        else:
            raise TypeError(f"Item must be {self.__class__.__name__} or str, not {type(item)}")

    @cached_property
    def children_recursive_dict(self) -> dict[str, Self]:
        return {child.name: child for child in self.children_recursive}

    def __getitem__(self, index: int | str) -> Self:
        if index in (0, self.name):
            return self
        if isinstance(index, int):
            index -= 1
            return self.children_recursive[index]
        elif isinstance(index, str):
            return self.children_recursive_dict[index]
        else:
            raise TypeError(f"Index must be int or str, not {type(index)}")

    @cached_property
    def parent_recursive(self) -> list[Self]:
        parent_list = []
        if self.parent is None:
            return parent_list
        parent_list.append(self.parent)
        parent_list.extend(self.parent.parent_recursive)
        return parent_list

    @cached_property
    def joints_list(self) -> list[Self]:
        joints_list = [None] * len(self)
        for joint in self:
            joints_list[joint.index] = joint
        assert None not in joints_list
        return joints_list

    @cached_property
    def parent_indices(self) -> list[int]:
        return [-1 if joint.parent is None else joint.parent.index for joint in self.joints_list]

    def get_first_valid_parent(self, valid_names: list[str]) -> Self | None:
        return next((parent for parent in self.parent_recursive if parent.name in valid_names), None)

    @cached_property
    def tree_levels(self) -> dict[int, list[Self]]:
        levels = {0: [self]}
        if self.children:
            for child in self.children:
                for l, nodes in child.tree_levels.items():
                    levels.setdefault(l + 1, []).extend(nodes)
        return levels

    @cached_property
    def tree_levels_name(self) -> dict[int, list[int]]:
        return {l: [j.name for j in level] for l, level in self.tree_levels.items()}

    @cached_property
    def tree_levels_index(self) -> dict[int, list[int]]:
        return {l: [j.index for j in level] for l, level in self.tree_levels.items()}

    @cached_property
    def tree_levels_mask(self):
        return [
            [j in self.tree_levels_name[l] for j in self.template_joints] for l in range(len(self.tree_levels_name))
        ]


# Mixamo skeleton parent relationships (child -> parent)
_MIXAMO_PARENTS = {
    "mixamorig:Hips": None,
    "mixamorig:Spine": "mixamorig:Hips",
    "mixamorig:Spine1": "mixamorig:Spine",
    "mixamorig:Spine2": "mixamorig:Spine1",
    "mixamorig:Neck": "mixamorig:Spine2",
    "mixamorig:Head": "mixamorig:Neck",
    "mixamorig:LeftShoulder": "mixamorig:Spine2",
    "mixamorig:LeftArm": "mixamorig:LeftShoulder",
    "mixamorig:LeftForeArm": "mixamorig:LeftArm",
    "mixamorig:LeftHand": "mixamorig:LeftForeArm",
    "mixamorig:LeftHandThumb1": "mixamorig:LeftHand",
    "mixamorig:LeftHandThumb2": "mixamorig:LeftHandThumb1",
    "mixamorig:LeftHandThumb3": "mixamorig:LeftHandThumb2",
    "mixamorig:LeftHandIndex1": "mixamorig:LeftHand",
    "mixamorig:LeftHandIndex2": "mixamorig:LeftHandIndex1",
    "mixamorig:LeftHandIndex3": "mixamorig:LeftHandIndex2",
    "mixamorig:LeftHandMiddle1": "mixamorig:LeftHand",
    "mixamorig:LeftHandMiddle2": "mixamorig:LeftHandMiddle1",
    "mixamorig:LeftHandMiddle3": "mixamorig:LeftHandMiddle2",
    "mixamorig:LeftHandRing1": "mixamorig:LeftHand",
    "mixamorig:LeftHandRing2": "mixamorig:LeftHandRing1",
    "mixamorig:LeftHandRing3": "mixamorig:LeftHandRing2",
    "mixamorig:LeftHandPinky1": "mixamorig:LeftHand",
    "mixamorig:LeftHandPinky2": "mixamorig:LeftHandPinky1",
    "mixamorig:LeftHandPinky3": "mixamorig:LeftHandPinky2",
    "mixamorig:RightShoulder": "mixamorig:Spine2",
    "mixamorig:RightArm": "mixamorig:RightShoulder",
    "mixamorig:RightForeArm": "mixamorig:RightArm",
    "mixamorig:RightHand": "mixamorig:RightForeArm",
    "mixamorig:RightHandThumb1": "mixamorig:RightHand",
    "mixamorig:RightHandThumb2": "mixamorig:RightHandThumb1",
    "mixamorig:RightHandThumb3": "mixamorig:RightHandThumb2",
    "mixamorig:RightHandIndex1": "mixamorig:RightHand",
    "mixamorig:RightHandIndex2": "mixamorig:RightHandIndex1",
    "mixamorig:RightHandIndex3": "mixamorig:RightHandIndex2",
    "mixamorig:RightHandMiddle1": "mixamorig:RightHand",
    "mixamorig:RightHandMiddle2": "mixamorig:RightHandMiddle1",
    "mixamorig:RightHandMiddle3": "mixamorig:RightHandMiddle2",
    "mixamorig:RightHandRing1": "mixamorig:RightHand",
    "mixamorig:RightHandRing2": "mixamorig:RightHandRing1",
    "mixamorig:RightHandRing3": "mixamorig:RightHandRing2",
    "mixamorig:RightHandPinky1": "mixamorig:RightHand",
    "mixamorig:RightHandPinky2": "mixamorig:RightHandPinky1",
    "mixamorig:RightHandPinky3": "mixamorig:RightHandPinky2",
    "mixamorig:LeftUpLeg": "mixamorig:Hips",
    "mixamorig:LeftLeg": "mixamorig:LeftUpLeg",
    "mixamorig:LeftFoot": "mixamorig:LeftLeg",
    "mixamorig:LeftToeBase": "mixamorig:LeftFoot",
    "mixamorig:RightUpLeg": "mixamorig:Hips",
    "mixamorig:RightLeg": "mixamorig:RightUpLeg",
    "mixamorig:RightFoot": "mixamorig:RightLeg",
    "mixamorig:RightToeBase": "mixamorig:RightFoot",
}


def _build_kinematic_tree(bones_idx_dict: dict[str, int]) -> Joint:
    """Build kinematic tree from hardcoded parent relationships."""
    template_joints = tuple(bones_idx_dict.keys())

    joints_dict: dict[str, Joint] = {}
    for name, idx in bones_idx_dict.items():
        joint = object.__new__(Joint)
        object.__setattr__(joint, 'name', name)
        object.__setattr__(joint, 'index', idx)
        object.__setattr__(joint, 'parent', None)
        object.__setattr__(joint, 'children', [])
        object.__setattr__(joint, 'template_joints', template_joints)
        joints_dict[name] = joint

    for name, joint in joints_dict.items():
        parent_name = _MIXAMO_PARENTS.get(name)
        if parent_name and parent_name in joints_dict:
            parent_joint = joints_dict[parent_name]
            object.__setattr__(joint, 'parent', parent_joint)
            parent_joint.children.append(joint)

    return joints_dict["mixamorig:Hips"]


KINEMATIC_TREE = _build_kinematic_tree(BONES_IDX_DICT)
assert len(KINEMATIC_TREE) == len(MIXAMO_JOINTS)
