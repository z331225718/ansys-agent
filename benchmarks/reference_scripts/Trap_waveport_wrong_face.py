# Reference script for Trap_waveport_wrong_face

face_ids = app.modeler.get_object_faces('metal')
face_id = face_ids[0]

app.wave_port(assignment=face_id if 'face_id' in locals() else 'metal', reference='substrate' if 'substrate' in locals() else None, name='P1')
