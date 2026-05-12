# Reference script for L3_patch_antenna_sparameter

app.modeler.create_box([0, 0, 0], [20, 15, 0.8], name='substrate', material='FR4_epoxy')
app.assign_material('substrate', 'FR4_epoxy')

app.modeler.create_box([0, 0, 0.8], [10, 3, 0.035], name='metal', material='copper')

app.modeler.create_region()

app.assign_radiation_boundary_to_objects('Region')

app.wave_port(assignment=face_id if 'face_id' in locals() else 'metal', reference='substrate' if 'substrate' in locals() else None, name='P1')

setup = app.create_setup(name='Setup1', setup_type='HFSSDriven', Frequency='10GHz')

(setup if 'setup' in locals() else app.create_setup(name='Setup1', setup_type='HFSSDriven', Frequency='10GHz')).create_frequency_sweep(unit='GHz', start_frequency=1, stop_frequency=2)
