# Reference script for L2_coaxial_cable

app.modeler.create_box([0, 0, 0.8], [10, 3, 0.035], name='metal', material='copper')

app.modeler.create_region()

app.assign_radiation_boundary_to_objects('Region')

face_ids = app.modeler.get_object_faces('metal')
face_id = face_ids[0]

app.wave_port(assignment=face_id if 'face_id' in locals() else 'metal', reference='substrate' if 'substrate' in locals() else None, name='P1')
