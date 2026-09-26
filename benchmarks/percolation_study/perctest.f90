! Compare Poreblazer's cluster labelling with exact periodic 6-connected components.
program perctest
    use percolation, only: clusteranalysis
    implicit none
    integer, parameter :: n = 40
    integer*2 :: grid(n, n, n)
    integer :: cluster(n, n, n), parent(n*n*n)
    integer, allocatable :: cl(:), trcl(:)
    integer :: nc, trial, i, j, k, a, b, nsplit, ntrue, bad
    real :: r(n, n, n), p
    integer :: rep_of(n*n*n), root_to_pb(n*n*n)

    allocate(cl(5000000), trcl(5000000))
    call random_seed()
    bad = 0
    do trial = 1, 60
        p = 0.2 + 0.01 * trial
        call random_number(r)
        grid = 0
        where (r < p) grid = 1
        cl = 0; trcl = 0; cluster = 0; nc = 0
        call clusteranalysis(grid, cluster, cl, trcl, nc)

        ! exact components by union-find with periodic 6-neighbours
        do i = 1, n*n*n
            parent(i) = i
        end do
        do k = 1, n; do j = 1, n; do i = 1, n
            if (grid(i,j,k) == 0) cycle
            a = idx(i,j,k)
            if (grid(modulo(i,n)+1,j,k) == 1) call unite(a, idx(modulo(i,n)+1,j,k))
            if (grid(i,modulo(j,n)+1,k) == 1) call unite(a, idx(i,modulo(j,n)+1,k))
            if (grid(i,j,modulo(k,n)+1) == 1) call unite(a, idx(i,j,modulo(k,n)+1))
        end do; end do; end do

        ! Poreblazer's partition matches iff each exact component has one label and
        ! each label belongs to one exact component
        root_to_pb = 0; rep_of = 0; nsplit = 0; ntrue = 0
        do k = 1, n; do j = 1, n; do i = 1, n
            if (grid(i,j,k) == 0) cycle
            b = find(idx(i,j,k))
            if (root_to_pb(b) == 0) then
                ntrue = ntrue + 1
                root_to_pb(b) = cluster(i,j,k)
            else if (root_to_pb(b) /= cluster(i,j,k)) then
                nsplit = nsplit + 1
            end if
            if (rep_of(cluster(i,j,k)) == 0) then
                rep_of(cluster(i,j,k)) = b
            else if (rep_of(cluster(i,j,k)) /= b) then
                nsplit = nsplit + 1
            end if
        end do; end do; end do
        if (nsplit > 0) bad = bad + 1
        if (mod(trial, 10) == 0 .or. nsplit > 0 .and. bad <= 5) &
            print '(a,f5.2,a,i7,a,i7,a,i8)', 'p=', p, '  exact clusters', ntrue, '  poreblazer', nc, &
                '  mismatched sites', nsplit
    end do
    print '(a,i3,a)', 'lattices where the labelling differs from exact components: ', bad, ' of 60'
contains
    integer function idx(i, j, k)
        integer, intent(in) :: i, j, k
        idx = i + n*(j-1) + n*n*(k-1)
    end function
    recursive integer function find(x) result(root)
        integer, intent(in) :: x
        if (parent(x) == x) then
            root = x
        else
            root = find(parent(x))
            parent(x) = root
        end if
    end function
    subroutine unite(x, y)
        integer, intent(in) :: x, y
        integer :: rx, ry
        rx = find(x); ry = find(y)
        if (rx /= ry) parent(max(rx, ry)) = min(rx, ry)
    end subroutine
end program perctest
