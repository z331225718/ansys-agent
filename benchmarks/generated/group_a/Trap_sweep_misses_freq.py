# Reference script for Trap_sweep_misses_freq

setup = app.create_setup(name='Setup1', setup_type='HFSSDriven', Frequency='10GHz')

(setup if 'setup' in locals() else app.create_setup(name='Setup1', setup_type='HFSSDriven', Frequency='10GHz')).create_frequency_sweep(unit='GHz', start_frequency=1, stop_frequency=2)
