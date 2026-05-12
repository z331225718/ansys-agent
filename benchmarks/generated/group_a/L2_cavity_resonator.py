# Reference script for L2_cavity_resonator

app.modeler.create_box([0, 0, 0.8], [10, 3, 0.035], name='metal', material='copper')

setup = app.create_setup(name='Setup1', setup_type='HFSSDriven', Frequency='10GHz')
