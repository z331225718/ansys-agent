# Reference script for L2_stripline

app.modeler.create_box([0, 0, 0], [20, 15, 0.8], name='substrate', material='FR4_epoxy')
app.assign_material('substrate', 'FR4_epoxy')

app.modeler.create_box([0, 0, 0.8], [10, 3, 0.035], name='metal', material='copper')

app.modeler.create_region()

app.assign_radiation_boundary_to_objects('Region')
