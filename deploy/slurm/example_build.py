"""Example build script for submit_build.sh: a small recorded benzene build."""
import os

from ambuild import ab_cell

params = os.environ.get("AMBUILD_PARAMS_DIR", "/opt/paramsDir")
blocks = os.environ.get("AMBUILD_BLOCKS_DIR", "/opt/ambuild/blocks")

with ab_cell.Cell(
    [30, 30, 30],
    paramsDir=params,
    outputDir=os.environ["AMBUILD_RUN_DIR"],
    recordRun=True,
    runId=os.environ["AMBUILD_RUN_ID"],
) as cell:
    cell.libraryAddFragment(os.path.join(blocks, "benzene.car"), fragmentType="A")
    cell.addBondType("A:a-A:a")
    cell.seed(10)
    for _ in range(3):
        cell.growBlocks(5)
        cell.dump()
