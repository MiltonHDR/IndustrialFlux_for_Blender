
bl_info = {
    "name": "Industrial Flux",
    "author": "MiltonHDR",
    "version": (1, 4, 5),
    "blender": (4, 5, 0),
    "location": "3D View > Sidebar > Industrial Flux",
    "description": "Rig by topology, rename mesh data, instances on curve, physics constraints, material parameters, lifting eye placement, create curve between connectors",
    "category": "Object",
}

import bpy
import math
from mathutils import Vector, Matrix

# -------------------- Common utils --------------------
def ensure_object_mode():
    if bpy.ops.object.mode_set.poll():
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            pass

def ensure_collection(name: str):
    coll = bpy.data.collections.get(name)
    if not coll:
        coll = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(coll)
    return coll

def unlink_from_all_collections(obj):
    for coll in list(obj.users_collection):
        try:
            coll.objects.unlink(obj)
        except Exception:
            pass

# =====================================================
# 1) Rig by Topology (igual à sua base)
# =====================================================
MIN_LEN   = 0.5
RIG_NAME  = "Rig"
ROOT_NAME = "root"
PREFIX    = "b_"

def nearest_selected_ancestor(obj, selected_set):
    p = obj.parent
    while p:
        if p.name in selected_set:
            return p
        p = p.parent
    return None

def create_simple_rig():
    ensure_object_mode()

    sel_objs = [o for o in bpy.context.selected_objects if o.type != 'ARMATURE']
    if not sel_objs:
        raise Exception("Selecione ao menos um objeto (não-ARMATURE).")

    selected_names = {o.name for o in sel_objs}
    obj_data = [(o, o.matrix_world.to_translation().copy()) for o in sel_objs]
    orig_world = {o.name: o.matrix_world.copy() for o in sel_objs}

    bpy.ops.object.armature_add(enter_editmode=True, location=(0.0, 0.0, 0.0))
    rig = bpy.context.object
    rig.name = RIG_NAME
    arm = rig.data
    eb = arm.edit_bones

    if len(eb) == 0:
        root_bone = eb.new(ROOT_NAME)
    else:
        root_bone = eb[0]
        root_bone.name = ROOT_NAME

    root_bone.head = (0.0, 0.0, 0.0)
    root_bone.tail = (0.0, 0.0, 1.0)

    obj_to_bone_name = {}
    for obj, obj_loc in obj_data:
        bone_name = f"{PREFIX}{obj.name}"
        bone = eb.new(bone_name)
        bone.head = obj_loc
        bone.tail = (obj_loc.x, obj_loc.y, obj_loc.z + MIN_LEN)
        bone.use_connect = False
        obj_to_bone_name[obj.name] = bone_name

    for obj, _ in obj_data:
        child_bone = eb[obj_to_bone_name[obj.name]]
        ancestor = nearest_selected_ancestor(obj, selected_names)
        if ancestor is not None:
            parent_bone = eb[obj_to_bone_name[ancestor.name]]
            child_bone.parent = parent_bone
        else:
            child_bone.parent = root_bone
        child_bone.use_connect = False

    bpy.ops.object.mode_set(mode='OBJECT')
    rig.show_in_front = True

    pose_bones = rig.pose.bones
    for obj, _ in obj_data:
        bone_name = obj_to_bone_name[obj.name]
        pb = pose_bones.get(bone_name)
        if not pb:
            continue
        obj.parent = rig
        obj.parent_type = 'BONE'
        obj.parent_bone = bone_name
        bone_world = rig.matrix_world @ pb.matrix
        obj.matrix_parent_inverse = bone_world.inverted()
        obj.matrix_world = orig_world[obj.name]

# =====================================================
# 2) Rename Mesh Data
# =====================================================
def rename_mesh_data_with_object_names():
    for obj in bpy.context.selected_objects:
        if obj.type == 'MESH':
            obj.data.name = obj.name

# =====================================================
# 3) Instances on Curve (GN)
# =====================================================
CURVE_GN = "GN_CurveInstances"
CUBE_GN  = "GN_ParametricCube"
CUBE_NAME = "ParametricCube"

def safe_set_enum(node, attr, value):
    if hasattr(node, attr):
        try: setattr(node, attr, value)
        except Exception: pass

def sock_in(node, name, idx=0):
    try: return node.inputs[name]
    except Exception: return node.inputs[idx]

def sock_out(node, name, idx=0):
    try: return node.outputs[name]
    except Exception: return node.outputs[idx]

def set_gn_modifier_input_by_name(mod, socket_name, value):
    ng = mod.node_group
    idx = 0
    ident = None
    for item in ng.interface.items_tree:
        if item.item_type == 'SOCKET' and item.in_out == 'INPUT':
            this_ident = getattr(item, "identifier", "") or f"Socket_{idx+1}"
            if item.name == socket_name:
                ident = this_ident
                break
            idx += 1
    if not ident:
        raise RuntimeError(f"Input '{socket_name}' não encontrado no modifier.")
    mod[ident] = value

def ensure_curve_instances_group():
    ng = bpy.data.node_groups.get(CURVE_GN)
    if ng:
        return ng

    ng = bpy.data.node_groups.new(CURVE_GN, 'GeometryNodeTree')

    itf = ng.interface
    itf.new_socket(name="Curve",      in_out='INPUT',  socket_type='NodeSocketGeometry')
    itf.new_socket(name="Collection", in_out='INPUT',  socket_type='NodeSocketCollection')
    s_count = itf.new_socket(name="Count", in_out='INPUT', socket_type='NodeSocketInt')
    try: s_count.min_value = 1; s_count.default_value = 20
    except Exception: pass
    s_scale = itf.new_socket(name="Scale", in_out='INPUT', socket_type='NodeSocketFloat')
    try: s_scale.default_value = 1.0
    except Exception: pass
    itf.new_socket(name="Geometry",   in_out='OUTPUT', socket_type='NodeSocketGeometry')

    nodes = ng.nodes; links = ng.links
    nodes.clear()

    n_in  = nodes.new('NodeGroupInput');   n_in.location  = (-1000,   0)
    n_out = nodes.new('NodeGroupOutput');  n_out.location = (  520,    0)

    n_resample = nodes.new('GeometryNodeResampleCurve'); n_resample.location = (-760, 0)
    safe_set_enum(n_resample, "mode", 'COUNT')

    n_col = nodes.new('GeometryNodeCollectionInfo'); n_col.location = (-760, -260)
    if hasattr(n_col, "separate_children"): n_col.separate_children = True
    else:
        if "Separate Children" in n_col.inputs: n_col.inputs["Separate Children"].default_value = True
    if hasattr(n_col, "reset_children"): n_col.reset_children = True
    else:
        if "Reset Children" in n_col.inputs: n_col.inputs["Reset Children"].default_value = True
    safe_set_enum(n_col, "transform_space", 'RELATIVE')

    n_tangent  = nodes.new('GeometryNodeInputTangent');          n_tangent.location  = (-360, 160)
    n_alignrot = nodes.new('FunctionNodeAlignRotationToVector'); n_alignrot.location = (-160, 160)
    safe_set_enum(n_alignrot, "axis", 'Z')
    if "Factor" in n_alignrot.inputs: n_alignrot.inputs["Factor"].default_value = 1.0

    n_inst = nodes.new('GeometryNodeInstanceOnPoints'); n_inst.location = (-160, -160)
    if hasattr(n_inst, "pick_instance"): n_inst.pick_instance = True
    else:
        if "Pick Instance" in n_inst.inputs: n_inst.inputs["Pick Instance"].default_value = True

    n_sep = nodes.new('GeometryNodeSeparateComponents'); n_sep.location = (180, -160)

    links = ng.links
    links.new(sock_out(n_in, 'Curve', 0),  sock_in(n_resample, 'Curve', 0))
    links.new(sock_out(n_in, 'Count', 2),  sock_in(n_resample, 'Count', 2))
    links.new(sock_out(n_in, 'Collection', 1), sock_in(n_col, 'Collection', 0))
    links.new(sock_out(n_in, 'Scale', 3),  sock_in(n_inst, 'Scale', 3))

    links.new(sock_out(n_resample, 'Curve', 0), sock_in(n_inst, 'Points', 0))
    links.new(sock_out(n_col, 'Geometry', 0),  sock_in(n_inst, 'Instance', 1))

    links.new(sock_out(n_tangent, 'Tangent', 0), sock_in(n_alignrot, 'Vector', 1))
    links.new(sock_out(n_alignrot, 'Rotation', 0), sock_in(n_inst, 'Rotation', 2))

    links.new(sock_out(n_inst, 'Instances', 0),  sock_in(n_sep, 'Geometry', 0))
    links.new(sock_out(n_sep, 'Instances', 4),   n_out.inputs[0])

    return ng

def apply_curve_instances_to_active_curve():
    obj = bpy.context.active_object
    if not obj or obj.type != 'CURVE':
        raise RuntimeError("Selecione UMA curva (objeto do tipo CURVE) antes de rodar.")
    ng = ensure_curve_instances_group()
    mod = obj.modifiers.get("Curve Instances")
    if not mod or mod.type != 'NODES':
        mod = obj.modifiers.new(name="Curve Instances", type='NODES')
    mod.node_group = ng
    for k, v in (("Count", 20), ("Scale", 1.0)):
        try: mod[k] = v
        except Exception: pass
    return obj, mod

def ensure_parametric_cube_group():
    ng = bpy.data.node_groups.get(CUBE_GN)
    if ng:
        return ng

    ng = bpy.data.node_groups.new(CUBE_GN, 'GeometryNodeTree')

    itf = ng.interface
    size_xy = itf.new_socket(name="Size XY", in_out='INPUT', socket_type='NodeSocketFloat')
    try: size_xy.default_value = 0.05
    except Exception: pass
    size_z  = itf.new_socket(name="Size Z",  in_out='INPUT', socket_type='NodeSocketFloat')
    try: size_z.default_value = 0.2
    except Exception: pass
    trans_z = itf.new_socket(name="Translate Z", in_out='INPUT', socket_type='NodeSocketFloat')
    try: trans_z.default_value = 0.0
    except Exception: pass
    itf.new_socket(name="Material", in_out='INPUT', socket_type='NodeSocketMaterial')
    itf.new_socket(name="Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')

    nodes = ng.nodes; links = ng.links
    nodes.clear()

    n_in  = nodes.new('NodeGroupInput');   n_in.location  = (-900, 0)
    n_out = nodes.new('NodeGroupOutput');  n_out.location = ( 500, 0)

    n_cube = nodes.new('GeometryNodeMeshCube'); n_cube.location = (-520, 0)
    try:
        n_cube.inputs['Vertices X'].default_value = 2
        n_cube.inputs['Vertices Y'].default_value = 2
        n_cube.inputs['Vertices Z'].default_value = 2
    except Exception: pass

    n_size = nodes.new('ShaderNodeCombineXYZ'); n_size.location = (-700, 120)
    links.new(n_in.outputs['Size XY'], n_size.inputs[0])
    links.new(n_in.outputs['Size XY'], n_size.inputs[1])
    links.new(n_in.outputs['Size Z'],  n_size.inputs[2])
    links.new(n_size.outputs['Vector'], n_cube.inputs['Size'])

    n_setmat = nodes.new('GeometryNodeSetMaterial'); n_setmat.location = (-200, 0)
    links.new(n_cube.outputs['Mesh'], n_setmat.inputs['Geometry'])
    links.new(n_in.outputs['Material'], n_setmat.inputs['Material'])

    n_trf = nodes.new('GeometryNodeTransform'); n_trf.location = (200, 0)
    n_trans = nodes.new('ShaderNodeCombineXYZ'); n_trans.location = (0, -120)
    n_trans.inputs[0].default_value = 0.0
    n_trans.inputs[1].default_value = 0.0
    links.new(n_in.outputs['Translate Z'], n_trans.inputs[2])
    links.new(n_setmat.outputs['Geometry'], n_trf.inputs['Geometry'])
    links.new(n_trans.outputs['Vector'],    n_trf.inputs['Translation'])

    links.new(n_trf.outputs['Geometry'], n_out.inputs[0])
    return ng

def get_or_create_parametric_cube_object(name=CUBE_NAME, place_at_cursor=True):
    ng = ensure_parametric_cube_group()
    coll = ensure_collection(name)
    obj = bpy.data.objects.get(name)
    if obj is None:
        mesh = bpy.data.meshes.new(name + "_Mesh")
        obj  = bpy.data.objects.new(name, mesh)
        coll.objects.link(obj)
        obj.location = bpy.context.scene.cursor.location if place_at_cursor else (0.0, 0.0, 0.0)
    else:
        if obj.name not in coll.objects:
            try:
                coll.objects.link(obj)
            except RuntimeError:
                pass
    mod = obj.modifiers.get("Parametric Cube")
    if not mod or mod.type != 'NODES':
        mod = obj.modifiers.new(name="Parametric Cube", type='NODES')
    mod.node_group = ng
    for k, v in (("Size XY", 0.05), ("Size Z", 0.2), ("Translate Z", 0.0)):
        try: mod[k] = v
        except Exception: pass
    return obj, coll, mod

def pipeline_instances_then_cube_and_bind():
    curve_obj, curve_mod = apply_curve_instances_to_active_curve()
    cube_obj, cube_coll, _ = get_or_create_parametric_cube_object(name=CUBE_NAME, place_at_cursor=True)
    set_gn_modifier_input_by_name(curve_mod, "Collection", cube_coll)
    return curve_obj, cube_obj, cube_coll

# =====================================================
# 4) Physics Constraints (versão 1.4.1)
# =====================================================
EMPTY_TYPE = 'PLAIN_AXES'
EMPTY_SCALE = 0.2
RB_ACTIVE = True
RB_MASS = 10.0
RB_COLLISION_SHAPE = 'MESH'
RB_USE_MARGIN = True
RB_MARGIN = 0.05
CONSTRAINT_TYPE = 'GENERIC'
LOCK_LINEAR_AT_ZERO = True
ANGLE_DEG = 45.0
ORDER_MODE = 'SUFFIX'
CLOSE_LOOP = False
ONLY_SELECTED = True
PHYSICS_MESHES_COLL_NAME = "Physics meshes"
PHYSICS_CONS_COLL_NAME   = "Physics Constraints"
ORIGINAL_CURVE_COLL_NAME = "original curve"

def extract_suffix_number(name):
    if "." in name:
        tail = name.rsplit(".", 1)[-1]
        if tail.isdigit():
            return int(tail)
    return None

def ensure_mesh_rigidbody(obj):
    if obj.rigid_body is None:
        prev_active = bpy.context.view_layer.objects.active
        bpy.context.view_layer.objects.active = obj
        try:
            bpy.ops.rigidbody.object_add()
        finally:
            bpy.context.view_layer.objects.active = prev_active
    rb = obj.rigid_body
    rb.type = 'ACTIVE' if RB_ACTIVE else 'PASSIVE'
    rb.mass = RB_MASS
    rb.collision_shape = RB_COLLISION_SHAPE
    rb.use_margin = RB_USE_MARGIN
    rb.collision_margin = RB_MARGIN

def ensure_constraint(obj, ctype='GENERIC'):
    if obj.rigid_body_constraint is None:
        prev_active = bpy.context.view_layer.objects.active
        bpy.context.view_layer.objects.active = obj
        try:
            bpy.ops.rigidbody.constraint_add(type=ctype)
        finally:
            bpy.context.view_layer.objects.active = prev_active
    obj.rigid_body_constraint.type = ctype
    return obj.rigid_body_constraint

def set_generic_limits(con, angle_deg=45.0, lock_linear_zero=True):
    ang = math.radians(angle_deg)
    for ax in ('x', 'y', 'z'):
        setattr(con, f'use_limit_lin_{ax}', True)
        if lock_linear_zero:
            setattr(con, f'limit_lin_{ax}_lower', 0.0)
            setattr(con, f'limit_lin_{ax}_upper', 0.0)
    for ax in ('x', 'y', 'z'):
        setattr(con, f'use_limit_ang_{ax}', True)
        setattr(con, f'limit_ang_{ax}_lower', -ang)
        setattr(con, f'limit_ang_{ax}_upper',  ang)

def get_or_create_empty_for(mesh_obj):
    target_name = f"{mesh_obj.name}_phys_const"
    empty = bpy.data.objects.get(target_name)
    if empty and empty.type == 'EMPTY':
        return empty
    empty = bpy.data.objects.new(target_name, None)
    empty.empty_display_type = EMPTY_TYPE
    empty.empty_display_size = EMPTY_SCALE
    empty.matrix_world = mesh_obj.matrix_world.copy()
    base_coll = mesh_obj.users_collection[0] if mesh_obj.users_collection else bpy.context.scene.collection
    if empty.name not in base_coll.objects:
        base_coll.objects.link(empty)
    return empty

def collect_meshes():
    objs = bpy.context.selected_objects if ONLY_SELECTED else bpy.context.scene.objects
    return [o for o in objs if o.type == 'MESH']

def sort_meshes(meshes, mode='SUFFIX'):
    if mode == 'SUFFIX':
        with_idx, no_idx = [], []
        for m in meshes:
            idx = extract_suffix_number(m.name)
            (with_idx if idx is not None else no_idx).append((idx, m))
        with_idx.sort(key=lambda t: t[0])
        no_idx.sort(key=lambda t: t[1].name)
        return [m for _, m in with_idx] + [m for _, m in no_idx]
    if mode == 'NAME':
        return sorted(meshes, key=lambda o: o.name.lower())
    if mode in ('X', 'Y', 'Z'):
        axis = {'X': 0, 'Y': 1, 'Z': 2}[mode]
        return sorted(meshes, key=lambda o: o.matrix_world.translation[axis])
    return sorted(meshes, key=lambda o: o.name.lower())

def pre_make_instances_real_and_archive_curve():
    ensure_object_mode()
    curve = bpy.context.active_object
    if not curve or curve.type != 'CURVE':
        return []
    before = set(bpy.data.objects)
    bpy.ops.object.select_all(action='DESELECT')
    curve.select_set(True)
    bpy.context.view_layer.objects.active = curve
    try:
        bpy.ops.object.duplicates_make_real()
    except Exception:
        pass
    after = set(bpy.data.objects)
    created = [o for o in (after - before) if o.type == 'MESH']

    orig_coll = ensure_collection(ORIGINAL_CURVE_COLL_NAME)
    unlink_from_all_collections(curve)
    if curve.name not in orig_coll.objects:
        orig_coll.objects.link(curve)

    bpy.ops.object.select_all(action='DESELECT')
    for o in created:
        o.select_set(True)
    if created:
        bpy.context.view_layer.objects.active = created[-1]
    return created

def run_physics_constraints():
    created_meshes = pre_make_instances_real_and_archive_curve()
    meshes = created_meshes if created_meshes else collect_meshes()
    if not meshes:
        return 0, 0, 0
    coll_phys_meshes = ensure_collection(PHYSICS_MESHES_COLL_NAME)
    coll_phys_cons   = ensure_collection(PHYSICS_CONS_COLL_NAME)
    pair_list = []
    for m in meshes:
        ensure_mesh_rigidbody(m)
        if m.name not in coll_phys_meshes.objects:
            try: coll_phys_meshes.objects.link(m)
            except RuntimeError: pass
        e = get_or_create_empty_for(m)
        con = ensure_constraint(e, CONSTRAINT_TYPE)
        set_generic_limits(con, ANGLE_DEG, LOCK_LINEAR_AT_ZERO)
        if e.name not in coll_phys_cons.objects:
            try: coll_phys_cons.objects.link(e)
            except RuntimeError: pass
        pair_list.append((m, e))
    meshes_sorted = sort_meshes([p[0] for p in pair_list], ORDER_MODE)
    mesh_to_empty = {m.name: e for (m, e) in pair_list}
    total_links = 0
    for i, mesh_curr in enumerate(meshes_sorted):
        if i == 0 and not CLOSE_LOOP:
            continue
        mesh_prev = meshes_sorted[i - 1] if i > 0 else meshes_sorted[-1]
        empty_curr = mesh_to_empty.get(mesh_curr.name, None)
        if not empty_curr: continue
        con = empty_curr.rigid_body_constraint or ensure_constraint(empty_curr, CONSTRAINT_TYPE)
        set_generic_limits(con, ANGLE_DEG, LOCK_LINEAR_AT_ZERO)
        con.object1 = mesh_prev
        con.object2 = mesh_curr
        total_links += 1
    return len(meshes), len(pair_list), total_links

# =====================================================
# 5) Set Material Parameters (popup resumido)
# =====================================================
def find_input_by_name(node, name: str):
    for s in node.inputs:
        if s.name == name:
            return s
    return None

def clamp01(x): return max(0.0, min(1.0, float(x)))

class IFX_OT_set_bsdf_params(bpy.types.Operator):
    bl_idname = "ifx.set_bsdf_params_popup"
    bl_label = "Set Material Parameters"
    bl_options = {'REGISTER', 'UNDO'}

    apply_roughness: bpy.props.BoolProperty(default=True)
    apply_metallic: bpy.props.BoolProperty(default=True)
    roughness: bpy.props.FloatProperty(default=0.5, min=0.0, max=1.0, subtype='FACTOR')
    metallic: bpy.props.FloatProperty(default=0.0, min=0.0, max=1.0, subtype='FACTOR')

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=360)

    def execute(self, context):
        materials = set()
        for obj in context.selected_objects:
            for slot in obj.material_slots:
                if slot.material:
                    materials.add(slot.material)
        for mat in materials:
            if mat.use_nodes and mat.node_tree:
                for n in mat.node_tree.nodes:
                    if n.type == 'BSDF_PRINCIPLED':
                        sR = find_input_by_name(n, "Roughness")
                        sM = find_input_by_name(n, "Metallic")
                        if sR and self.apply_roughness and not sR.is_linked:
                            sR.default_value = self.roughness
                        if sM and self.apply_metallic and not sM.is_linked:
                            sM.default_value = self.metallic
            mat.roughness = clamp01(self.roughness)
            mat.metallic  = clamp01(self.metallic)
        self.report({'INFO'}, "Material params applied.")
        return {'FINISHED'}

# =====================================================
# 6) Place Lifting Eye (v2.0.0 - templates + legacy default)
# =====================================================
def duplicate_from_template_object(template: bpy.types.Object, target_collection=None):
    dup = template.copy()
    if hasattr(template, "data") and template.data:
        dup.data = template.data.copy()
    dup.name = template.name
    if target_collection is None:
        target_collection = bpy.context.scene.collection
    try:
        target_collection.objects.link(dup)
    except RuntimeError:
        pass
    dup.rotation_mode = template.rotation_mode
    if dup.rotation_mode == 'QUATERNION':
        dup.rotation_quaternion = template.rotation_quaternion.copy()
    elif dup.rotation_mode == 'AXIS_ANGLE':
        dup.rotation_axis_angle = template.rotation_axis_angle[:]
    else:
        dup.rotation_euler = template.rotation_euler.copy()
    dup.scale = template.scale.copy()
    return dup

def parent_keep_transform(child: bpy.types.Object, parent: bpy.types.Object):
    mw_child = child.matrix_world.copy()
    child.parent = parent
    child.matrix_parent_inverse = parent.matrix_world.inverted() @ mw_child

def set_world_location(obj: bpy.types.Object, world_pos: Vector):
    mw = obj.matrix_world.copy()
    mw.translation = world_pos
    obj.matrix_world = mw

def add_child_of(owner: bpy.types.Object, target: bpy.types.Object, cname: str, influence: float = 1.0):
    con = owner.constraints.new('CHILD_OF')
    con.name = cname
    con.target = target
    con.use_location_x = con.use_location_y = con.use_location_z = True
    con.use_rotation_x = con.use_rotation_y = con.use_rotation_z = True
    con.use_scale_x = con.use_scale_y = con.use_scale_z = True
    con.influence = influence
    owner.select_set(True)
    bpy.context.view_layer.objects.active = owner
    owner.constraints.active = con
    try:
        bpy.ops.constraint.childof_set_inverse(constraint=con.name, owner='OBJECT')
    except Exception as e:
        print(f"[WARN] childof_set_inverse: {e}")
    return con

def add_damped_track(owner: bpy.types.Object, target: bpy.types.Object, cname: str, track_axis='TRACK_Z'):
    dt = owner.constraints.new('DAMPED_TRACK')
    dt.name = cname
    dt.target = target
    dt.track_axis = track_axis
    return dt

def add_limit_rotation_z_range(owner: bpy.types.Object, min_deg=-15.0, max_deg=15.0):
    c = owner.constraints.new('LIMIT_ROTATION')
    c.name = "LimitRot_Z_range"
    c.owner_space = 'LOCAL'
    c.use_limit_x = False
    c.use_limit_y = False
    c.use_limit_z = True
    c.min_z = math.radians(min_deg)
    c.max_z = math.radians(max_deg)
    return c

def add_limit_rotation_allow_axis(owner: bpy.types.Object, allow_axis: str):
    c = owner.constraints.new('LIMIT_ROTATION')
    c.name = f"LimitRot_{allow_axis}_only"
    c.owner_space = 'LOCAL'
    c.use_limit_x = True; c.min_x = 0.0; c.max_x = 0.0
    c.use_limit_y = True; c.min_y = 0.0; c.max_y = 0.0
    c.use_limit_z = True; c.min_z = 0.0; c.max_z = 0.0
    if allow_axis == 'X':
        c.use_limit_x = False
    elif allow_axis == 'Y':
        c.use_limit_y = False
    else:
        c.use_limit_z = False
    return c

def bbox_top_center_world(obj: bpy.types.Object):
    mw = obj.matrix_world
    wverts = [mw @ Vector(v) for v in obj.bound_box]
    max_z = max(v.z for v in wverts)
    eps = 1e-6
    top_verts = [v for v in wverts if (max_z - v.z) <= eps] or wverts
    cx = sum(v.x for v in top_verts) / len(top_verts)
    cy = sum(v.y for v in top_verts) / len(top_verts)
    return Vector((cx, cy, max_z))

def bbox_bottom_center_world(obj: bpy.types.Object):
    mw = obj.matrix_world
    wverts = [mw @ Vector(v) for v in obj.bound_box]
    min_z = min(v.z for v in wverts)
    eps = 1e-6
    bottom_verts = [v for v in wverts if (v.z - min_z) <= eps] or wverts
    cx = sum(v.x for v in bottom_verts) / len(bottom_verts)
    cy = sum(v.y for v in bottom_verts) / len(bottom_verts)
    return Vector((cx, cy, min_z))

def link_targets_collection_of(obj_like):
    cols = list(obj_like.users_collection)
    return cols[0] if cols else bpy.context.scene.collection

def build_eye_from_templates_at_empty(bolt_tmpl, swivel_tmpl, ring_tmpl, empty):
    target_collection = link_targets_collection_of(empty)
    bolt = duplicate_from_template_object(bolt_tmpl, target_collection)
    swivel = duplicate_from_template_object(swivel_tmpl, target_collection)
    ring = duplicate_from_template_object(ring_tmpl, target_collection)
    bolt.parent = None; swivel.parent = None; ring.parent = None
    parent_keep_transform(swivel, bolt)
    parent_keep_transform(ring, swivel)
    mw = bolt.matrix_world.copy(); mw.translation = empty.matrix_world.translation; bolt.matrix_world = mw
    roots = [bolt]
    return bolt, swivel, ring, roots

class IFX_OT_place_lifting_eye_dialog(bpy.types.Operator):
    bl_idname = "ifx.place_lifting_eye_dialog"
    bl_label = "Place Lifting Eye"
    bl_options = {'REGISTER', 'UNDO'}

    threaded_bolt_object_name: bpy.props.StringProperty(name="Threaded_Bolt (template)", default="")
    swivel_head_object_name: bpy.props.StringProperty(name="Swivel_Head (template)", default="")
    load_ring_object_name: bpy.props.StringProperty(name="Load_Ring (template)", default="")

    connector_object_name: bpy.props.StringProperty(name="Connector Object", default="")
    master_link_object_name: bpy.props.StringProperty(name="Master Link Object", default="")
    master_link_offset: bpy.props.FloatProperty(name="Master Link Offset above top (m)", default=4.0, soft_min=0.0)

    snap_connector_to_bottom: bpy.props.BoolProperty(name="Snap Connector to Master Link Bottom", default=True)
    connector_bottom_clearance: bpy.props.FloatProperty(name="Clearance along -Z of Master Link (m)", default=0.03, min=0.0, soft_max=0.2)

    direction: bpy.props.EnumProperty(
        name="Child Of Direction",
        items=[
            ('EYE_FROM_PIECE', "Eye follows Piece (legacy)", "Child Of no Bolt → Peça"),
            ('PIECE_FROM_EYE', "Piece follows Eye (recommended)", "Child Of na Peça → Bolt"),
        ],
        default='EYE_FROM_PIECE'
    )
    distribute_influence: bpy.props.BoolProperty(name="Distribuir influência (1/N) na peça", default=True)
    apply_empty_rotation: bpy.props.BoolProperty(name="Apply Empty Rotation (after constraints)", default=True)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=720)

    def draw(self, context):
        layout = self.layout
        box = layout.box(); box.label(text="Lifting Eye Templates", icon='OUTLINER_OB_MESH')
        box.prop_search(self, "threaded_bolt_object_name", bpy.data, "objects", text="Threaded_Bolt")
        box.prop_search(self, "swivel_head_object_name", bpy.data, "objects", text="Swivel_Head")
        box.prop_search(self, "load_ring_object_name", bpy.data, "objects", text="Load_Ring")

        box2 = layout.box(); box2.label(text="Connector & Master Link", icon='OUTLINER_OB_EMPTY')
        row = box2.row(align=True)
        row.prop_search(self, "connector_object_name", bpy.data, "objects", text="Connector Object")
        row.prop_search(self, "master_link_object_name", bpy.data, "objects", text="Master Link Object")
        box2.prop(self, "master_link_offset")
        row = box2.row(align=True)
        row.prop(self, "snap_connector_to_bottom")
        row.prop(self, "connector_bottom_clearance")

        box3 = layout.box(); box3.label(text="Selection & Constraints", icon='CONSTRAINT')
        box3.label(text="• Ativo = PEÇA (último selecionado). • Selecione também os empties (qualquer nome).", icon='INFO')
        box3.prop(self, "direction")
        if self.direction == 'PIECE_FROM_EYE':
            box3.prop(self, "distribute_influence")
        box3.prop(self, "apply_empty_rotation")

    def execute(self, context):
        piece = context.view_layer.objects.active
        if not piece:
            self.report({'ERROR'}, "Defina a PEÇA como ativo (último selecionado).")
            return {'CANCELLED'}

        bolt_tmpl = bpy.data.objects.get(self.threaded_bolt_object_name)
        swivel_tmpl = bpy.data.objects.get(self.swivel_head_object_name)
        ring_tmpl = bpy.data.objects.get(self.load_ring_object_name)
        if not (bolt_tmpl and swivel_tmpl and ring_tmpl):
            self.report({'ERROR'}, "Informe os 3 templates: Threaded_Bolt, Swivel_Head e Load_Ring.")
            return {'CANCELLED'}

        connector_template = bpy.data.objects.get(self.connector_object_name)
        if not connector_template:
            self.report({'ERROR'}, f"Connector Object '{self.connector_object_name}' não encontrado.")
            return {'CANCELLED'}

        master_link_template = bpy.data.objects.get(self.master_link_object_name)
        if not master_link_template:
            self.report({'ERROR'}, f"Master Link Object '{self.master_link_object_name}' não encontrado.")
            return {'CANCELLED'}

        empties = [o for o in context.selected_objects if o.type == 'EMPTY']
        if not empties:
            self.report({'ERROR'}, "Selecione ao menos um EMPTY (qualquer nome).")
            return {'CANCELLED'}

        created = 0; childofs = 0; dtracks = 0; limits_added = 0; aims_created = 0
        pending_piece_childofs = []; connectors_created = []

        top_center = bbox_top_center_world(piece)
        master_link_pos = Vector((top_center.x, top_center.y, top_center.z + self.master_link_offset))
        master_link_dup = duplicate_from_template_object(master_link_template)
        set_world_location(master_link_dup, master_link_pos)

        piece_pos = piece.matrix_world.translation
        initial_connector_world = Vector((piece_pos.x, piece_pos.y, piece_pos.z + 4.0))

        target_infl = (1.0 / len(empties)) if (self.direction == 'EYE_FROM_PIECE' and self.distribute_influence and len(empties) > 0) else 1.0

        for e in empties:
            bolt_dup, swivel_dup, ring_dup, roots_dup = build_eye_from_templates_at_empty(
                bolt_tmpl, swivel_tmpl, ring_tmpl, e
            )
            connector_dup = duplicate_from_template_object(connector_template)
            set_world_location(connector_dup, initial_connector_world)
            parent_keep_transform(connector_dup, master_link_dup)
            connectors_created.append(connector_dup)

            lr_aim = None
            if ring_dup:
                lr_aim = bpy.data.objects.new(name=f"LR_Aim_{ring_dup.name}", object_data=None)
                lr_aim.empty_display_type = 'PLAIN_AXES'
                lr_aim.empty_display_size = 0.08
                link_targets_collection_of = lambda o: (list(o.users_collection)[0] if o.users_collection else bpy.context.scene.collection)
                link_targets_collection_of(ring_dup).objects.link(lr_aim)
                lr_aim.parent = ring_dup
                lr_aim.location = (0,0,0)
                aims_created += 1

            if lr_aim:
                add_damped_track(owner=connector_dup, target=lr_aim, cname=f"DT_Connector_to_LR_Aim@{e.name}", track_axis='TRACK_NEGATIVE_Z')
                dtracks += 1
            add_limit_rotation_z_range(owner=connector_dup, min_deg=-15.0, max_deg=15.0); limits_added += 1

            if ring_dup:
                add_damped_track(owner=ring_dup, target=connector_dup, cname=f"DT_LoadRing@{e.name}", track_axis='TRACK_Z'); dtracks += 1

            if swivel_dup:
                add_damped_track(owner=swivel_dup, target=connector_dup, cname=f"DT_Swivel@{e.name}", track_axis='TRACK_X'); dtracks += 1
                add_limit_rotation_allow_axis(owner=swivel_dup, allow_axis='Z'); limits_added += 1

            if self.direction == 'EYE_FROM_PIECE':
                if bolt_dup:
                    con = add_child_of(owner=piece, target=bolt_dup, cname=f"ChildOf_Eye@{e.name}", influence=0.0)
                    pending_piece_childofs.append(con); childofs += 1
            else:
                if bolt_dup:
                    add_child_of(owner=bolt_dup, target=piece, cname=f"ChildOf_Piece@{piece.name}", influence=1.0); childofs += 1

            if self.apply_empty_rotation and roots_dup:
                q = e.matrix_world.to_quaternion()
                for r in roots_dup:
                    r.rotation_mode = 'QUATERNION'
                    r.rotation_quaternion = q

            created += 1

        if self.direction == 'EYE_FROM_PIECE' and pending_piece_childofs:
            for con in pending_piece_childofs:
                con.influence = target_infl

        if self.snap_connector_to_bottom and connectors_created:
            bottom_world = bbox_bottom_center_world(master_link_dup)
            ml_neg_z_world = (master_link_dup.matrix_world.to_quaternion() @ Vector((0, 0, -1))).normalized()
            bottom_world_with_clearance = bottom_world + ml_neg_z_world * self.connector_bottom_clearance
            for conn in connectors_created:
                set_world_location(conn, bottom_world_with_clearance)

        self.report({'INFO'}, f"Olhais: {created} | Aims: {aims_created} | Damped Track: {dtracks} | Limits: {limits_added} | Child Of: {childofs}")
        return {'FINISHED'}

# =====================================================
# 7) Create Curve Between Connectors (novo botão)
# =====================================================
def link_same_collection_as(obj_like):
    cols = list(obj_like.users_collection)
    return cols[0] if cols else bpy.context.scene.collection

def bbox_extreme_world(obj: bpy.types.Object, pick: str = "TOP") -> Vector:
    bpy.context.view_layer.update()
    mw = obj.matrix_world
    local = [Vector(v) for v in obj.bound_box]
    if pick.upper() == "TOP":
        zmax = max(v.z for v in local)
        face = [v for v in local if abs(v.z - zmax) <= 1e-6]
    else:
        zmin = min(v.z for v in local)
        face = [v for v in local if abs(v.z - zmin) <= 1e-6]
    cx = sum(v.x for v in face) / len(face)
    cy = sum(v.y for v in face) / len(face)
    cz = face[0].z
    return mw @ Vector((cx, cy, cz))

def parent_keep_transform_curve(child: bpy.types.Object, parent: bpy.types.Object):
    world = child.matrix_world.copy()
    child.parent = parent
    child.matrix_parent_inverse = parent.matrix_world.inverted() @ world
    child.matrix_world = world

def create_anchor(parent_obj: bpy.types.Object, name: str, world_pos: Vector, size=0.06):
    col = link_same_collection_as(parent_obj)
    e = bpy.data.objects.new(name=name, object_data=None)
    e.empty_display_type = 'PLAIN_AXES'
    e.empty_display_size = size
    col.objects.link(e)
    e.matrix_world = Matrix.Translation(world_pos)
    parent_keep_transform_curve(e, parent_obj)
    return e

def create_poly_curve_2pts(name: str, p0_world: Vector, p1_world: Vector, target_collection=None):
    cdata = bpy.data.curves.new(name=name, type='CURVE')
    cdata.dimensions = '3D'
    spl = cdata.splines.new('POLY')
    spl.points.add(1)
    curve_obj = bpy.data.objects.new(name, cdata)
    (target_collection or bpy.context.scene.collection).objects.link(curve_obj)
    curve_obj.matrix_world = Matrix.Translation(p0_world)
    inv = curve_obj.matrix_world.inverted()
    p0_local = inv @ p0_world
    p1_local = inv @ p1_world
    spl.points[0].co = (p0_local.x, p0_local.y, p0_local.z, 1.0)
    spl.points[1].co = (p1_local.x, p1_local.y, p1_local.z, 1.0)
    return curve_obj

def add_copy_location(owner: bpy.types.Object, target: bpy.types.Object, name="CopyLoc"):
    c = owner.constraints.new('COPY_LOCATION')
    c.name = name
    c.target = target
    c.owner_space = 'WORLD'
    c.target_space = 'WORLD'
    return c

def assign_hook_to_point(curve_obj: bpy.types.Object, point_index: int, target_obj: bpy.types.Object, hook_name="Hook"):
    mod = curve_obj.modifiers.new(hook_name, type='HOOK')
    mod.object = target_obj
    mod.strength = 1.0
    view = bpy.context.view_layer
    active_prev = view.objects.active
    for o in view.objects:
        o.select_set(False)
    curve_obj.select_set(True)
    view.objects.active = curve_obj
    try:
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.curve.select_all(action='DESELECT')
        curve_obj.data.splines[0].points[point_index].select = True
        bpy.ops.object.hook_assign(modifier=mod.name)
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')
        if active_prev:
            view.objects.active = active_prev
    return mod

def has_dt_to(owner: bpy.types.Object, target: bpy.types.Object):
    for c in owner.constraints:
        if c.type == 'DAMPED_TRACK' and getattr(c, "target", None) is target:
            if getattr(c, "mute", False): continue
            if getattr(c, "influence", 1.0) <= 0.0: continue
            return True
    return False

class IFX_OT_make_link_curves_dt_only(bpy.types.Operator):
    bl_idname = "ifx.create_curve_between_connectors"
    bl_label = "Create Curve Between Connectors"
    bl_description = "Cria curvas entre pares detectados por Damped Track (A DT→B): A=Load_Ring, B=Connector."
    bl_options = {'REGISTER', 'UNDO'}

    anchor_size: bpy.props.FloatProperty(name="Anchor size (m)", default=0.06, min=0.0, soft_max=0.2)
    curve_prefix: bpy.props.StringProperty(name="Curve name prefix", default="ConnLR_Curve")
    conn_anchor_prefix: bpy.props.StringProperty(name="Connector anchor prefix", default="Conn_BottomAnchor")
    lr_anchor_prefix: bpy.props.StringProperty(name="LoadRing anchor prefix", default="LR_TopAnchor")

    @classmethod
    def poll(cls, context):
        return sum(1 for o in context.selected_objects if o.type not in {'EMPTY'}) >= 2

    def execute(self, context):
        candidates = [o for o in context.selected_objects if o.type not in {'EMPTY'}]
        if not candidates:
            self.report({'ERROR'}, "Selecione pelo menos 2 objetos (excluindo empties).")
            return {'CANCELLED'}

        bpy.context.view_layer.update()
        pairs = set()
        for a in candidates:
            for b in candidates:
                if a is b: continue
                if has_dt_to(a, b):
                    pairs.add((b, a))  # (connector, ring)

        if not pairs:
            self.report({'WARNING'}, "Nenhum par encontrado por Damped Track entre os objetos selecionados.")
            return {'CANCELLED'}

        made = 0
        for connector, ring in pairs:
            conn_bottom = bbox_extreme_world(connector, pick="BOTTOM")
            ring_top = bbox_extreme_world(ring, pick="TOP")

            conn_anchor = create_anchor(connector, f"{self.conn_anchor_prefix}", conn_bottom, size=self.anchor_size)
            lr_anchor = create_anchor(ring, f"{self.lr_anchor_prefix}", ring_top, size=self.anchor_size)

            curve_obj = create_poly_curve_2pts(f"{self.curve_prefix}", conn_bottom, ring_top,
                                               target_collection=link_same_collection_as(connector))

            add_copy_location(curve_obj, conn_anchor, name="CopyLoc_ConnAnchor")
            assign_hook_to_point(curve_obj, point_index=1, target_obj=lr_anchor, hook_name=f"Hook_{lr_anchor.name}")
            made += 1

        self.report({'INFO'}, f"Curvas criadas: {made}")
        return {'FINISHED'}

# =====================================================
# UI Panel
# =====================================================
class IFX_PT_panel(bpy.types.Panel):
    bl_label = "Industrial Flux"
    bl_idname = "IFX_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Industrial Flux"
    def draw(self, context):
        layout = self.layout
        layout.operator("ifx.create_rig", icon="ARMATURE_DATA", text="Rig by Topology")
        layout.operator("ifx.rename_mesh_data", icon="OUTLINER_DATA_MESH", text="Rename Mesh Data")
        layout.operator("ifx.instances_on_curve", icon="CURVE_DATA", text="Instances on Curve")
        layout.operator("ifx.physics_constraints", icon="CONSTRAINT", text="Set Physics Constraints")
        layout.separator()
        layout.operator("ifx.set_bsdf_params_popup", icon="MATERIAL", text="Set Material Parameters")
        layout.operator("ifx.place_lifting_eye_dialog", icon="OUTLINER_OB_EMPTY", text="Place Lifting Eye")
        layout.operator("ifx.create_curve_between_connectors", icon="CURVE_DATA", text="Create Curve Between Connectors")

# Operators wrappers
class IFX_OT_create_rig(bpy.types.Operator):
    bl_idname = "ifx.create_rig"
    bl_label = "Rig by Topology"; bl_options = {'REGISTER','UNDO'}
    def execute(self, context):
        try:
            create_simple_rig()
            self.report({'INFO'}, "Rig criado.")
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, str(e)); return {'CANCELLED'}

class IFX_OT_rename_mesh(bpy.types.Operator):
    bl_idname = "ifx.rename_mesh_data"
    bl_label = "Rename Mesh Data"; bl_options = {'REGISTER','UNDO'}
    def execute(self, context):
        rename_mesh_data_with_object_names(); self.report({'INFO'},"Mesh Data renomeada."); return {'FINISHED'}

class IFX_OT_instances_on_curve(bpy.types.Operator):
    bl_idname = "ifx.instances_on_curve"
    bl_label = "Instances on Curve"; bl_options = {'REGISTER','UNDO'}
    def execute(self, context):
        try:
            pipeline_instances_then_cube_and_bind()
            self.report({'INFO'}, "Instances on Curve aplicado.")
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, str(e)); return {'CANCELLED'}

class IFX_OT_physics_constraints(bpy.types.Operator):
    bl_idname = "ifx.physics_constraints"
    bl_label = "Set Physics Constraints"; bl_options = {'REGISTER','UNDO'}
    def execute(self, context):
        m, e, l = run_physics_constraints()
        self.report({'INFO'}, f"Physics: meshes={m}, empties={e}, links={l}")
        return {'FINISHED'}

# register
classes = (
    IFX_OT_create_rig,
    IFX_OT_rename_mesh,
    IFX_OT_instances_on_curve,
    IFX_OT_physics_constraints,
    IFX_OT_set_bsdf_params,
    IFX_OT_place_lifting_eye_dialog,
    IFX_OT_make_link_curves_dt_only,
    IFX_PT_panel,
)

def register():
    for c in classes:
        bpy.utils.register_class(c)

def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)

if __name__ == "__main__":
    register()
