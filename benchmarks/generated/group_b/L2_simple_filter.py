# Reference script for L2_simple_filter

app.modeler.create_box([0, 0, 0.8], [10, 3, 0.035], name='metal', material='copper')

face_ids = app.modeler.get_object_faces('metal')
face_id = face_ids[0]

app.wave_port(assignment=face_id if 'face_id' in locals() else 'metal', reference='substrate' if 'substrate' in locals() else None, name='P1')

setup = app.create_setup(name='Setup1', setup_type='HFSSDriven', Frequency='10GHz')
