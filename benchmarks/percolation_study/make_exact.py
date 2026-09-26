# Experimental: replace Poreblazer's cluster labelling with exact periodic union-find.
import sys

path = sys.argv[1]
s = open(path, newline="").read().replace("\r\n", "\n")
s = s.replace("Call clusteranalysis(lattice_in,cluster,cl,trcl,nc)",
              "Call clusteranalysis_exact(lattice_in,cluster,cl,nc)")
assert s.count("clusteranalysis_exact(lattice_in") == 2
exact = """
    Subroutine clusteranalysis_exact(ngrid, cluster, cl, nc)
        Integer*2, Dimension(:,:,:), Intent(In)   :: ngrid
        Integer, Dimension(:,:,:), Intent(InOut)  :: cluster
        Integer, Dimension(:), Intent(InOut)      :: cl
        Integer, Intent(InOut)                    :: nc
        Integer, Dimension(:), allocatable        :: parent
        Integer                                   :: i, j, k, LX, LY, LZ, s, r

        LX = size(ngrid,1)
        LY = size(ngrid,2)
        LZ = size(ngrid,3)
        allocate(parent(LX*LY*LZ))
        do s=1, LX*LY*LZ
            parent(s) = s
        end do
        do k=1, LZ
            do j=1, LY
                do i=1, LX
                    if(ngrid(i,j,k) /= 1) cycle
                    s = i + LX*(j-1) + LX*LY*(k-1)
                    if(ngrid(modulo(i,LX)+1,j,k) == 1) call unite(s, modulo(i,LX)+1 + LX*(j-1) + LX*LY*(k-1))
                    if(ngrid(i,modulo(j,LY)+1,k) == 1) call unite(s, i + LX*modulo(j,LY) + LX*LY*(k-1))
                    if(ngrid(i,j,modulo(k,LZ)+1) == 1) call unite(s, i + LX*(j-1) + LX*LY*modulo(k,LZ))
                end do
            end do
        end do
        ! Labels in order of each cluster's first site, as the original relabelling pass
        cluster = 0
        nc = 0
        do k=1, LZ
            do j=1, LY
                do i=1, LX
                    if(ngrid(i,j,k) /= 1) cycle
                    s = i + LX*(j-1) + LX*LY*(k-1)
                    r = find(s)
                    if(parent(r) > 0) then
                        nc = nc + 1
                        parent(r) = -nc
                    end if
                    cluster(i,j,k) = -parent(r)
                    cl(-parent(r)) = cl(-parent(r)) + 1
                end do
            end do
        end do
        deallocate(parent)
    contains
        integer function find(x)
            integer, intent(in) :: x
            integer :: y
            y = x
            do while(parent(y) > 0 .and. parent(y) /= y)
                if(parent(parent(y)) > 0) parent(y) = parent(parent(y))
                y = parent(y)
            end do
            find = y
        end function find
        subroutine unite(a, b)
            integer, intent(in) :: a, b
            integer :: ra, rb
            ra = find(a)
            rb = find(b)
            if(ra /= rb) parent(max(ra, rb)) = min(ra, rb)
        end subroutine unite
    End Subroutine clusteranalysis_exact
"""
marker = "     End Subroutine clusteranalysis\n"
assert s.count(marker) == 1
s = s.replace(marker, marker + exact)
open(path, "w", newline="").write(s)
print("ok")
