! Percolation labelling study: Poreblazer's labelling (module percolation, unchanged
! from the fork) against exact periodic union-find labelling (module percolation_exact,
! the same file with clusteranalysis_exact swapped in).
!
!   study random L p0 p1 dp trials seed      statistics on random site lattices
!   study grd FILE r0 r1 dr                   spanning against probe radius on a real grid
!   study dump FILE r                         labels of the largest exact cluster at radius r
program study
    use percolation, only: up_ca => clusteranalysis, up_simple => percolation_calc_simple
    use percolation_exact, only: ex_ca => clusteranalysis_exact, ex_simple => percolation_calc_simple
    implicit none
    character(len=256) :: mode, fname, arg
    integer :: L, trials, seed, t, nup, nex, i, j, k, nx, ny, nz, spu, spe, nbad, nsites
    real :: p0, p1, dp, p, r0, r1, dr, r
    integer*2, allocatable :: grid(:,:,:), work(:,:,:)
    integer, allocatable :: cu(:,:,:), ce(:,:,:), clu(:), cle(:), trcl(:), summary(:,:)
    real, allocatable :: rnd(:,:,:), field(:,:,:)
    integer, allocatable :: seedarr(:)
    real*8 :: bad, excess, pspu, pspe, wrong
    integer :: nseed

    call get_command_argument(1, mode)
    allocate(summary(20, 2))

    if (trim(mode) == 'random') then
        call get_command_argument(2, arg); read(arg, *) L
        call get_command_argument(3, arg); read(arg, *) p0
        call get_command_argument(4, arg); read(arg, *) p1
        call get_command_argument(5, arg); read(arg, *) dp
        call get_command_argument(6, arg); read(arg, *) trials
        call get_command_argument(7, arg); read(arg, *) seed
        call random_seed(size=nseed)
        allocate(seedarr(nseed)); seedarr = seed + 37 * [(i, i=1, nseed)]
        call random_seed(put=seedarr)
        allocate(grid(L,L,L), work(L,L,L), cu(L,L,L), ce(L,L,L), rnd(L,L,L))
        allocate(clu(5000000), cle(5000000), trcl(5000000))
        print '(a)', '# L p lattices_mislabelled mean_excess_clusters_frac sites_in_split_clusters_frac p_span_up p_span_exact'
        p = p0
        do while (p <= p1 + 1e-6)
            bad = 0; excess = 0; pspu = 0; pspe = 0; wrong = 0
            do t = 1, trials
                call random_number(rnd)
                grid = 0
                where (rnd < p) grid = 1
                cu = 0; clu = 0; trcl = 0; nup = 0
                call up_ca(grid, cu, clu, trcl, nup)
                ce = 0; cle = 0; nex = 0
                call ex_ca(grid, ce, cle, nex)
                nsites = count(grid == 1)
                if (nup /= nex) bad = bad + 1
                if (nex > 0) excess = excess + dble(nup - nex) / dble(nex)
                wrong = wrong + dble(split_sites(ce, cu, cle, nex)) / max(1, nsites)
                work = grid; call up_simple(work, summary, spu)
                work = grid; call ex_simple(work, summary, spe)
                if (spu > 0) pspu = pspu + 1
                if (spe > 0) pspe = pspe + 1
            end do
            print '(i4, f7.3, 5f10.5)', L, p, bad/trials, excess/trials, wrong/trials, pspu/trials, pspe/trials
            p = p + dp
        end do

    else if (trim(mode) == 'grd' .or. trim(mode) == 'dump') then
        call get_command_argument(2, fname)
        call read_grd(fname)
        allocate(grid(nx,ny,nz), work(nx,ny,nz))
        if (trim(mode) == 'grd') then
            call get_command_argument(3, arg); read(arg, *) r0
            call get_command_argument(4, arg); read(arg, *) r1
            call get_command_argument(5, arg); read(arg, *) dr
            print '(a)', '# r_A sites spanning_up spanning_exact'
            r = r0
            do while (r <= r1 + 1e-6)
                grid = 0
                where (field >= r .and. field > 0) grid = 1
                work = grid; call up_simple(work, summary, spu)
                work = grid; call ex_simple(work, summary, spe)
                print '(f8.3, i10, 2i4)', r, count(grid == 1), spu, spe
                r = r + dr
            end do
        else
            call get_command_argument(3, arg); read(arg, *) r
            allocate(cu(nx,ny,nz), ce(nx,ny,nz), clu(5000000), cle(5000000), trcl(5000000))
            grid = 0
            where (field >= r .and. field > 0) grid = 1
            cu = 0; clu = 0; trcl = 0; nup = 0
            call up_ca(grid, cu, clu, trcl, nup)
            ce = 0; cle = 0; nex = 0
            call ex_ca(grid, ce, cle, nex)
            call dump_largest()
        end if
    end if

contains

    ! Sites whose Poreblazer label is shared with, or split from, their exact cluster:
    ! the sites of every exact cluster that Poreblazer does not label as one piece
    integer function split_sites(ce, cu, cle, nex)
        integer, intent(in) :: ce(:,:,:), cu(:,:,:), cle(:), nex
        integer, allocatable :: first(:)
        logical, allocatable :: split(:)
        integer :: i, j, k
        allocate(first(nex), split(nex))
        first = 0; split = .false.
        do k = 1, size(ce, 3); do j = 1, size(ce, 2); do i = 1, size(ce, 1)
            if (ce(i,j,k) == 0) cycle
            if (first(ce(i,j,k)) == 0) then
                first(ce(i,j,k)) = cu(i,j,k)
            else if (first(ce(i,j,k)) /= cu(i,j,k)) then
                split(ce(i,j,k)) = .true.
            end if
        end do; end do; end do
        split_sites = 0
        do i = 1, nex
            if (split(i)) split_sites = split_sites + cle(i)
        end do
    end function

    subroutine read_grd(fname)
        character(len=*), intent(in) :: fname
        character(len=256) :: line
        integer :: u
        real :: cell(6)
        open(newunit=u, file=fname, status='old')
        read(u, '(a)') line
        read(u, '(a)') line
        read(u, *) cell
        read(u, *) nx, ny, nz
        read(u, '(a)') line
        nx = nx + 1; ny = ny + 1; nz = nz + 1
        allocate(field(nx, ny, nz))
        read(u, *) field
        close(u)
    end subroutine

    ! The largest exact cluster: its size, how many Poreblazer labels it is split into,
    ! the sizes of those pieces, and a projection along z of the piece at each (x, y)
    subroutine dump_largest()
        integer :: big, n, pieces, lab, idx
        integer, allocatable :: piece_of(:), piece_size(:), proj(:,:)
        big = maxloc(cle(1:nex), 1)
        allocate(piece_of(nup), piece_size(nup), proj(nx, ny))
        piece_of = 0; piece_size = 0; proj = 0; pieces = 0
        ! number the pieces by size order later; first by first appearance
        do k = 1, nz; do j = 1, ny; do i = 1, nx
            if (ce(i,j,k) /= big) cycle
            lab = cu(i,j,k)
            if (piece_of(lab) == 0) then
                pieces = pieces + 1
                piece_of(lab) = pieces
            end if
            piece_size(piece_of(lab)) = piece_size(piece_of(lab)) + 1
        end do; end do; end do
        ! projection: the piece of the lowest site in each column (0 = none)
        do j = 1, ny; do i = 1, nx
            do k = 1, nz
                if (ce(i,j,k) == big) then
                    proj(i,j) = piece_of(cu(i,j,k))
                    exit
                end if
            end do
        end do; end do
        print '(a, i8)', 'SITES ', count(grid == 1)
        print '(a, i8, a, i8)', 'CLUSTERS_UP ', nup, ' CLUSTERS_EXACT ', nex
        print '(a, i8)', 'LARGEST ', cle(big)
        print '(a, i8)', 'PIECES ', pieces
        write(*, '(a)', advance='no') 'PIECE_SIZES'
        do n = 1, pieces
            write(*, '(1x, i0)', advance='no') piece_size(n)
        end do
        print *
        print '(a, 2i6)', 'PROJ ', nx, ny
        do j = 1, ny
            write(*, '(1000(i0, 1x))') (proj(i, j), i = 1, nx)
        end do
        idx = 0
    end subroutine

end program study
