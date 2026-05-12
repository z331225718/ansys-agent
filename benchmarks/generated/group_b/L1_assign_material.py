# Reference script for L1_assign_material

app.modeler.create_box([0, 0, 0], [20, 15, 0.8], name='substrate', material='FR4_epoxy')
app.assign_material('substrate', 'FR4_epoxy')
