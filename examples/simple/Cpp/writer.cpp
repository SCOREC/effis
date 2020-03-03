#include "adios2.h"
#include <mpi.h>
#include <cstdlib>

int main(int argc, char **argv)
{
    const std::size_t nelems = 10;
	int KnownInt, RandomInt, KnownInts[nelems], RandomInts[nelems];
	double RandomReals[nelems];

	MPI_Comm comm;
	int rank, nproc;
	double dt=0.1;
	adios2::Variable<int> vKnownInt;
	adios2::Variable<int> vRandomInt;

	MPI_Init(&argc, &argv);
	MPI_Comm_dup(MPI_COMM_WORLD, &comm);
	MPI_Comm_rank(comm, &rank);
	MPI_Comm_size(comm, &nproc);


    //@effis-init comm=comm
	adios2::ADIOS adios(comm, adios2::DebugON);

    if (rank == 0)
	{
        KnownInt = 666;
        RandomInt = rand() % 1000;
	}

	for (int i=0; i<nelems; i++)
	{
		KnownInts[i] = i;
	}

    //@effis-begin "Jabberwocky"->"Jaberwocky"
	adios2::IO io = adios.DeclareIO("Jabberwocky");

	adios2::Dims global{static_cast<size_t>(nproc), nelems};
	adios2::Dims offset{static_cast<size_t>(rank), 0};
	adios2::Dims local{1, nelems};

	adios2::Variable<int> vKnownInts  = io.DefineVariable<int>("KnownInts",  global, offset, local, adios2::ConstantDims);
    adios2::Variable<int> vRandomInts = io.DefineVariable<int>("RandomInts", global, offset, local, adios2::ConstantDims);
    adios2::Variable<double> vRandomReals = io.DefineVariable<double>("RandomReals", global, offset, local, adios2::ConstantDims);
    if (rank == 0)
	{
		vKnownInt  = io.DefineVariable<int>("KnownInt");
        vRandomInt = io.DefineVariable<int>("RandomInt");
	}

	io.SetEngine("SST");
	io.SetParameter("RendezvousReaderCount", "1");
	adios2::Engine engine = io.Open("Jabberwocky.bp", adios2::Mode::Write, comm);


    for (int j=0; j<10; j++)
	{
        //@effis-timer start="LoopTimer", comm=comm
		
		for (int i=0; i<nelems; i++)
		{
			RandomInts[i] = (rand() % 1000);
			RandomReals[i] = 1.0 * (rand() % 1000);
		}
        //@effis-timestep physical=j*dt, number=j
		
        engine.BeginStep();
		if ((rank == 0) && (j == 0))
		{
			engine.Put(vKnownInt,  KnownInt);
			engine.Put(vRandomInt, RandomInt);
		}
        engine.Put(vKnownInts,  KnownInts);
        engine.Put(vRandomInts, RandomInts);
        engine.Put(vRandomReals, RandomReals);
        engine.EndStep();

        //@effis-timer stop="LoopTimer"
	}

    engine.Close();
    //@effis-end

    //@effis-finalize
}

