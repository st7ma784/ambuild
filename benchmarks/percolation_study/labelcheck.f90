! Print Poreblazer's final cluster labels for each lattice on stdin ("L v1 v2 ...",
! values in Fortran order, i fastest), one line per lattice: "L l1 l2 ...".
program labelcheck
    use percolation, only: clusteranalysis
    implicit none
    integer :: L, ios, nc
    integer*2, allocatable :: grid(:,:,:)
    integer, allocatable :: cluster(:,:,:), cl(:), trcl(:)
    allocate(cl(5000000), trcl(5000000))
    do
        read(*, *, iostat=ios) L
        if (ios /= 0) exit
        backspace(5)
        allocate(grid(L,L,L), cluster(L,L,L))
        read(*, *) L, grid
        cluster = 0; cl = 0; trcl = 0; nc = 0
        call clusteranalysis(grid, cluster, cl, trcl, nc)
        write(*, '(i0, 100000(1x, i0))') L, cluster
        deallocate(grid, cluster)
    end do
end program labelcheck
