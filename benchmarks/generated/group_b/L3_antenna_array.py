# Reference script for L3_antenna_array

app.modeler.create_box([0, 0, 0.8], [10, 3, 0.035], name='metal', material='copper')

setup = app.create_setup(name='Setup1', setup_type='HFSSDriven', Frequency='10GHz')

(setup if 'setup' in locals() else app.create_setup(name='Setup1', setup_type='HFSSDriven', Frequency='10GHz')).create_frequency_sweep(unit='GHz', start_frequency=1, stop_frequency=2)
