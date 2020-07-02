# Fluid solver based on lattice boltzmann method using taichi language
# About taichi : https://github.com/taichi-dev/taichi
# Author : Wang (hietwll@gmail.com)
# - [Homework0 计算流体力学视角的流体求解器 - GAMES201 高级物理引擎实战 - Taichi](https://forum.taichi.graphics/t/homework0/506)
# 
# 涡量着色 by: woclass
#   https://forum.taichi.graphics/t/homework0/506/10?u=woclass
import taichi as ti
import numpy as np
import matplotlib
import matplotlib.cm as cm
import matplotlib.pyplot as plt

ti.init(arch=ti.gpu)

@ti.data_oriented
class lbm_solver:
    def __init__(self,
        nx: ti.i32, # domain size
        ny: ti.i32,
        niu: ti.f32, # viscosity of fluid
        bc_type, # [left,top,right,bottom] boundary conditions: 0 -> Dirichlet ; 1 -> Neumann
        bc_value, # if bc_type = 0, we need to specify the velocity in bc_value
        cy: ti.i32 = 0, # whether to place a cylindrical obstacle
        cy_para = [0.0, 0.0, 0.0], # location and radius of the cylinder
        steps: ti.i32 = 60000
    ): # total steps to run
        ## 参数
        self.nx = nx  # by convention, dx = dy = dt = 1.0 (lattice units)
        self.ny = ny
        self.niu = niu
        self.tau = 3.0 * niu + 0.5
        self.inv_tau = 1.0 / self.tau
        self.steps = steps
        self.w = ti.var(dt=ti.f32, shape=9)   
        self.e = ti.var(dt=ti.i32, shape=(9, 2))
        self.bc_type = ti.var(dt=ti.i32, shape=4)
        self.bc_value = ti.var(dt=ti.f32, shape=(4, 2))
        self.cy = cy
        self.cy_para = ti.var(dt=ti.f32, shape=3)
        
        ## 预分配变量
        self.rho = ti.var(dt=ti.f32, shape=(nx, ny))
        self.vel = ti.Vector(2, dt=ti.f32, shape=(nx, ny))
        self.mask = ti.var(dt=ti.f32, shape=(nx, ny))
        self.f_old = ti.Vector(9, dt=ti.f32, shape=(nx, ny))
        self.f_new = ti.Vector(9, dt=ti.f32, shape=(nx, ny))
        self.display_var = ti.var(dt=ti.f32, shape=(nx, ny))

        ## 赋值
        arr = np.array([ 
            4.0 /  9.0, 1.0 /  9.0, 1.0 /  9.0, 
            1.0 /  9.0, 1.0 /  9.0, 1.0 / 36.0,
            1.0 / 36.0, 1.0 / 36.0, 1.0 / 36.0
        ], dtype=np.float32)
        self.w.from_numpy(arr)
        arr = np.array([
            [ 0, 0], [ 1,  0], [0,  1], 
            [-1, 0], [ 0, -1], [1,  1],
            [-1, 1], [-1, -1], [1, -1]
        ], dtype=np.int32)
        self.e.from_numpy(arr)
        self.bc_type.from_numpy(np.array(bc_type, dtype=np.int32))
        self.bc_value.from_numpy(np.array(bc_value, dtype=np.float32))
        self.cy_para.from_numpy(np.array(cy_para, dtype=np.float32))


    @ti.func # compute equilibrium distribution function
    def f_eq(self, i, j, k):
        e, vel, w, rho = ti.static(self.e, self.vel, self.w, self.rho)
        eu = e[k, 0] * vel[i, j][0] + e[k, 1] * vel[i, j][1]
        uv = vel[i, j][0]**2.0 + vel[i, j][1]**2.0
        return w[k] * rho[i, j] * (1.0 + 3.0 * eu + 4.5 * eu**2 - 1.5 * uv)

    @ti.kernel
    def init(self):
        for i, j in self.rho:
            self.vel[i, j][0] = 0.0
            self.vel[i, j][1] = 0.0
            self.rho[i, j] = 1.0
            self.mask[i, j] = 0.0
            for k in ti.static(range(9)):
                self.f_new[i, j][k] = self.f_eq(i, j, k)
                self.f_old[i, j][k] = self.f_new[i, j][k]
            if(self.cy==1):
                if ((i - self.cy_para[0])**2.0 + (j - self.cy_para[1])**2.0 
                    <= self.cy_para[2]**2.0):
                    self.mask[i, j] = 1.0


    @ti.kernel
    def collide_and_stream(self): # lbm core equation
        for i, j in ti.ndrange((1, self.nx - 1), (1, self.ny - 1)):
            for k in ti.static(range(9)):
                ip = i - self.e[k, 0]
                jp = j - self.e[k, 1]
                self.f_new[i,j][k] = (1.0-self.inv_tau)*self.f_old[ip,jp][k] + \
                                        self.f_eq(ip,jp,k)*self.inv_tau

    @ti.kernel
    def update_macro_var(self): # compute rho u v
        for i, j in ti.ndrange((1, self.nx - 1), (1, self.ny - 1)):
            self.rho[i, j] = 0.0
            self.vel[i, j][0] = 0.0
            self.vel[i, j][1] = 0.0
            for k in ti.static(range(9)):
                self.f_old[i, j][k] = self.f_new[i, j][k]
                self.rho[i, j] += self.f_new[i, j][k]
                self.vel[i, j][0] += (self.e[k, 0] * self.f_new[i, j][k])
                self.vel[i, j][1] += (self.e[k, 1] * self.f_new[i, j][k])
            self.vel[i, j][0] /= self.rho[i, j]
            self.vel[i, j][1] /= self.rho[i, j]

    @ti.kernel
    def apply_bc(self): # impose boundary conditions
        # left and right
        for j in ti.ndrange(1, self.ny - 1):
            # left: dr = 0; ibc = 0; jbc = j; inb = 1; jnb = j
            self.apply_bc_core(1, 0, 0, j, 1, j)

            # right: dr = 2; ibc = nx-1; jbc = j; inb = nx-2; jnb = j
            self.apply_bc_core(1, 2, self.nx - 1, j, self.nx - 2, j)

        # top and bottom
        for i in ti.ndrange(self.nx):
            # top: dr = 1; ibc = i; jbc = ny-1; inb = i; jnb = ny-2
            self.apply_bc_core(1, 1, i, self.ny - 1, i, self.ny - 2)

            # bottom: dr = 3; ibc = i; jbc = 0; inb = i; jnb = 1
            self.apply_bc_core(1, 3, i, 0, i, 1)

        # cylindrical obstacle
        # Note: for cuda backend, putting 'if statement' inside loops can be much faster!
        for i, j in ti.ndrange(self.nx, self.ny): 
            if (self.cy == 1 and self.mask[i, j] == 1):
                self.vel[i, j][0] = 0.0  # velocity is zero at solid boundary  
                self.vel[i, j][1] = 0.0
                inb = 0
                jnb = 0
                if (i >= self.cy_para[0]):
                    inb = i + 1
                else:
                    inb = i - 1
                if (j >= self.cy_para[1]):
                    jnb = j + 1
                else:
                    jnb = j - 1
                self.apply_bc_core(0, 0, i, j, inb, jnb)

    @ti.func
    def apply_bc_core(self, outer, dr, ibc, jbc, inb, jnb):
        if (outer == 1):  # handle outer boundary
            if (self.bc_type[dr] == 0):
                self.vel[ibc, jbc][0] = self.bc_value[dr, 0]
                self.vel[ibc, jbc][1] = self.bc_value[dr, 1]
            elif (self.bc_type[dr] == 1):
                self.vel[ibc, jbc][0] = self.vel[inb, jnb][0]
                self.vel[ibc, jbc][1] = self.vel[inb, jnb][1]
        self.rho[ibc, jbc] = self.rho[inb, jnb]
        for k in ti.static(range(9)):
            self.f_old[ibc,jbc][k] = self.f_eq(ibc,jbc,k) \
                                    - self.f_eq(inb,jnb,k) \
                                    + self.f_old[inb,jnb][k]

    @ti.kernel
    def get_display_var(self):
        # get velocity magnitude
        for i, j in ti.ndrange(self.nx, self.ny):
            self.display_var[i, j] = ti.sqrt(self.vel[i, j][0]**2.0 +
                                             self.vel[i, j][1]**2.0)

    @ti.kernel
    def get_display_var_x(self):
        # get x-direction component only
        for i, j in ti.ndrange(self.nx, self.ny):
            self.display_var[i, j] = self.vel[i, j][0]

    def solve(self):
        gui = ti.GUI('lbm solver', (self.nx, self.ny*2))
        self.init()
        for i in range(self.steps):
            self.collide_and_stream()
            self.update_macro_var()
            self.apply_bc()
            self.get_display_var()

            ## vor
            vel = self.vel.to_numpy()
            ugrad = np.gradient(vel[:, :, 0])
            vgrad = np.gradient(vel[:, :, 1])
            du_dy = ugrad[1]
            dv_dx = vgrad[0]
            ## vor = dv/dx - du/dy
            vor = dv_dx - du_dy
            ## 颜色映射
            colors = [
                (1, 1, 0), 
                (0.953, 0.490, 0.016), 
                (0, 0, 0),
                (0.176, 0.976, 0.529), 
                (0, 1, 1)
            ]
            my_cmap = matplotlib.colors.LinearSegmentedColormap.from_list(
                'my_cmap', colors)
            vor_img = cm.ScalarMappable(cmap=my_cmap).to_rgba(vor)
            vel_img = cm.plasma(self.display_var.to_numpy() / 0.15)
    
            # numpy 的 y 方向貌似和 taichi 相反
            img = np.concatenate((vor_img, vel_img), axis=1)
            gui.set_image(img)
            
            # gui.show()
            gui.show(f'frame/{i:04d}.png')

            if (i % 1000 == 0):
                print('Step: {:}'.format(i))
                # ti.imwrite((img[:,:,0:3]*255).astype(np.uint8), 'fig/karman_'+str(i).zfill(6)+'.png')

    def pass_to_py(self):
        self.get_display_var_x()
        return self.display_var.to_numpy()


if __name__ == '__main__':
    flow_case = 0
    if (flow_case == 0):  # von Karman vortex street: Re = U*D/niu = 200
        # lbm = lbm_solver(401, 181, 0.005, [0, 0, 1, 0],
        #      [[0.1, 0.0], [0.0, 0.0], [0.0, 0.0], [0.0, 0.0]],
        #      1,[90.0, 90.0, 10.0])
        lbm = lbm_solver(401, 101, 0.005, [0, 0, 1, 0],
                         [[0.1, 0.0], [0.0, 0.0], [0.0, 0.0], [0.0, 0.0]],
                         1, [80.0, 50.0, 10.0])

        lbm.solve()
    
    elif (flow_case == 1):  # lid-driven cavity flow: Re = U*L/niu = 1000
        lbm = lbm_solver(256, 256, 0.0255, [0, 0, 0, 0],
                         [[0.0, 0.0], [0.1, 0.0], [0.0, 0.0], [0.0, 0.0]])
        lbm.solve()

        # compare with literature results
        y_ref, u_ref = np.loadtxt('data/ghia1982.dat', unpack=True, skiprows=2, usecols=(0, 2))
        fig, axes = plt.subplots(nrows=1, ncols=1, figsize=(4, 3), dpi=200)
        axes.plot(np.linspace(0, 1.0, 256), lbm.pass_to_py()[256 // 2, :] / 0.1, 'b-', label='LBM')
        axes.plot(y_ref, u_ref, 'rs', label='Ghia et al. 1982')
        axes.legend()
        axes.set_xlabel(r'Y')
        axes.set_ylabel(r'U')
        plt.tight_layout()
        plt.show()
