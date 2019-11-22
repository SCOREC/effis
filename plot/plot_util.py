import os
import yaml
import numpy as np

from mpi4py import MPI
import adios2

import sys
import time


def GetType(varid):
    size = varid.Sizeof()
    kind = varid.Type()

    # I'm just handling the common ones for now
    if kind.find('int') != -1:
        if size == 8:
            UserType = np.int64
        elif size == 4:
            UserType = np.int32
        elif size == 2:
            UserType = np.int16
    elif kind.find('double') != -1:
        UserType = np.float64
    elif (kind.find('float') != -1) or (kind.find('single') != -1):
        UserType = np.float32

    return UserType


def ShapeParse(xShape, xsel):
    counts = np.array(xShape, dtype=np.int64)
    starts = np.zeros(counts.shape[0], dtype=np.int64)
    if xsel is not None:
        for j, dim in enumerate(xShape):
            if (j >= len(xsel)) or (xsel[j] == ":"):
                continue
            else:
                if xsel[j].find(":") != -1:
                    start, stop = xsel[j].split(":")
                    starts[j] = int(start.strip())
                    counts[j] = int( stop.strip()) - start[j]
                else:
                    starts[j] = int(xsel[j])
                    counts[j] = 1
    return starts, counts


class KittiePlotter(object):
    
    def __init__(self, comm, on=False):
        self.comm = comm
        self.rank = self.comm.Get_rank()
        self.on = on

        yamlfile = ".kittie-groups-" + os.environ["KITTIE_NUM"] + ".yaml"
        with open(yamlfile, 'r') as ystream:
            self.config = yaml.load(ystream)

        # Handle MPMD if needed
        if "mpmd" in self.config:
            self.comm = comm.Split(self.config["mpmd"], self.rank)
            self.rank = self.comm.Get_rank()
            del self.config['mpmd']


    def _InitByCommSize(self):
        size = self.comm.Get_size()
        self.DimInfo['UserMatches'] = []
        self.DimInfo['UserTypes'] = []
        for i in range(size):
            self.DimInfo['UserMatches'] += [[]]
            self.DimInfo['UserTypes'] += [[]]
        return size


    def _GetMatching(self, exclude=[], only=[], xomit=False):
        vx = self.io.InquireVariable(self.DimInfo['xname'])
        shape = vx.Shape()
        if len(shape) == 0:
            raise ValueError("Using this with a scalar for the x-axis doesn't makes sense")
        self.DimInfo['xType'] = GetType(vx)

        variables = self.io.AvailableVariables()
        if len(only) == 0:
            only = variables.keys()
        if (self.DimInfo['xname'] not in only) and (not xomit):
            only += [self.DimInfo['xname']]

        size = self._InitByCommSize()
        index = 0

        for name in only:
            if (name in exclude) or ((name == self.DimInfo['xname']) and xomit):
                continue

            varid = self.io.InquireVariable(name)
            TestShape = varid.Shape()
            if shape == TestShape:
                i = index % size
                self.DimInfo['UserMatches'][i] += [name]
                dtype = GetType(varid)
                self.DimInfo['UserTypes'][i] += [dtype]
                index += 1

        return shape


    def _xParse(self, xname, getname=False):
        if getname:
            rname = xname
        else:
            self.DimInfo['xname'] = xname

        if xname.endswith(']'):
            start = xname.find('[')
            if getname:
                rname = xname[:start]
            else:
                self.DimInfo['xname'] = xname[:start]
            xdims = xname[start+1:-1]
            xdims = xdims.split(',')
            for i in range(len(xdims)):
                xdims[i] = xdims[i].strip()
        else:
            xdims = None

        if getname:
            return xdims, rname
        else:
            return xdims


    def _GetExplicit(self, xaxis, y):
        xsel, xname = self._xParse(xaxis, getname=True)
        ysel, yname = self._xParse(y, getname=True)
        size = self._InitByCommSize()
        index = 0
        for name in [xname, yname]:
            i = index % size
            varid = self.io.InquireVariable(name)
            if name == xname:
                xShape = varid.Shape()
                xtype = GetType(varid)
                self.DimInfo['xname'] = xname
                self.DimInfo['xType'] = xtype
            else:
                yShape = varid.Shape()
                ytype = GetType(varid)
                self.DimInfo['UserMatches'][i] += [name]
                self.DimInfo['UserTypes'][i] += [ytype]
            index += 1
        xstart, xcount = ShapeParse(xShape, xsel)
        ystart, ycount = ShapeParse(yShape, ysel)
        return xstart, xcount, xname, xtype, ystart, ycount, yname, ytype


    def _GetSelections(self, xaxis, exclude=[], only=[], xomit=False):

        # Get the name and slice
        xsel = self._xParse(xaxis)

        # Get the full shape and other variables that match it
        xShape = self._GetMatching(exclude=exclude, only=only, xomit=False)

        # Get ADIOS selections
        starts, counts = ShapeParse(xShape, xsel)
        self.DimInfo['starts'] = list(starts)
        self.DimInfo['counts'] = list(counts)


    def _SetupArrays(self, allx, explicit=False):
        self.uMatches = self.DimInfo['UserMatches']
        self.uTypes = self.DimInfo['UserTypes']
        if allx:
            self.uMatches += [self.DimInfo['xname']]
            self.uTypes += [self.DimInfo['xType']]

        self.data = {}
        self.data['_StepPhysical'] = np.zeros(1, dtype=np.float64)
        self.data['_StepNumber'] = np.zeros(1, dtype=np.int64)
        if not explicit:
            for name, dtype in zip(self.uMatches, self.uTypes):
                self.data[name] = np.zeros(tuple(self.DimInfo['counts']), dtype=dtype)


    def ConnectToStepInfo(self, adios, group=None):

        if group is None:
            self.gname = list(self.config.keys())[0]
        else:
            self.gname = group


        if (self.rank == 0) and self.on:
            yamlfile = ".kittie-codenames-" + os.environ["KITTIE_NUM"] + ".yaml"
            with open(yamlfile, 'r') as ystream:
                codeconfig = yaml.load(ystream)
            appname = codeconfig['codename']
            self.StepGroup = appname + "-step"
            StepFile = self.config[self.gname]['stepfile']
            self.LastStepFile = StepFile + ".done"

            self.SteppingDone = False
            self.StepEngine = None
            self.code, self.group = self.config[self.gname]['reads'].strip().split('.', 1)


            #@effis-begin self.StepGroup->self.StepGroup
            self.StepIO = adios.DeclareIO(self.StepGroup)
            """
            self.StepIO.SetEngine("SST")
            self.StepIO.SetParameter("MarshalMethod", "BP")
            self.StepIO.SetParameter("RendezvousReaderCount", "0")
            self.StepIO.SetParameter("QueueLimit", "1")
            self.StepIO.SetParameter("QueueFullPolicy", "Discard")
            """
            if not(os.path.exists(self.LastStepFile)):
                self.StepEngine = self.StepIO.Open(StepFile, adios2.Mode.Read, MPI.COMM_SELF)
                self.StepOpen = True
            else:
                self.SteppingDone = True
                self.StepOpen = False
            #@effis-end

        self.LastFoundData = np.array([-1], dtype=np.int64)
        self.LastFoundSim  = np.array([-1], dtype=np.int64)
        self.SecondLastFoundSim  = np.array([-1], dtype=np.int64)

        if (self.rank == 0) and self.on:

            #@effis-begin "done"->"done"
            self.DoneIO = adios.DeclareIO("done")
            self.DoneIO.SetEngine('BP4')
            self.vDone = self.DoneIO.DefineVariable("Step",  self.LastFoundSim,  [], [], [])
            name = "{0}-{1}-StepsDone.bp".format(appname, self.group)
            self.DoneEngine = self.DoneIO.Open(name, adios2.Mode.Write, MPI.COMM_SELF)
            #@effis-end


    @property
    def Active(self):
        if len(self.DimInfo['UserMatches']) > 0:
            return True
        else:
            return False


    def GetMatchingSelections(self, adios, xaxis, exclude=[], only=[], xomit=False, allx=True, y="match-dimensions"):
        self.DimInfo = {}
        for name in ["xname", "xType", "UserMatches", "UserTypes"]:
            self.DimInfo[name] = None
        if y == "match-dimensions":
            explicit = False
            for name in ["starts", "counts"]:
                self.DimInfo[name] = None
        else:
            explicit = True

        #@effis-begin self.gname->self.gname
        self.io = adios.DeclareIO(self.gname)
        if self.rank == 0:
            self.engine = self.io.Open(self.gname, adios2.Mode.Read, MPI.COMM_SELF)
            self.engine.BeginStep(kittie.Kittie.ReadStepMode, -1.0)
            self.io = kittie.Kittie.adios.AtIO(self.gname)
            if y == "match-dimensions":
                self._GetSelections(xaxis, exclude=exclude, only=only, xomit=xomit)
            else:
                xstart, xcount, xname, xtype, ystart, ycount, yname, ytype = self._GetExplicit(xaxis, y)

            self.engine.Close()
            self.io.RemoveAllVariables()
            self.io.RemoveAllAttributes()
        #@effis-end

        if y == "match-dimensions":
            for name in ['starts', 'counts']:
                self.DimInfo[name] = self.comm.bcast(self.DimInfo[name], root=0)
        for name in ['xname', 'xType']:
            self.DimInfo[name] = self.comm.bcast(self.DimInfo[name], root=0)
        for name in ['UserMatches', 'UserTypes']:
            self.DimInfo[name] = self.comm.scatter(self.DimInfo[name], root=0)

        if (self.rank == 0) and (not os.path.exists('images')):
            os.makedirs('images')

        # Only do something on the processes where there's a plot
        color = 0
        if len(self.DimInfo['UserMatches']) > 0:
            color = 1
        self.ReadComm = self.comm.Split(color, self.rank)

        if self.Active:
            self._SetupArrays(allx, explicit=explicit)
            if explicit:
                self.data[xname] = np.zeros(tuple(xcount), dtype=xtype)
                self.data[yname] = np.zeros(tuple(ycount), dtype=ytype)
                self.uStarts = [ystart, xstart]
                self.uCounts = [ycount, xcount]
            filename = None

            #@effis-begin self.io-->"plotter"
            self.engine = self.io.Open(filename, adios2.Mode.Read, self.ReadComm)
            #@effis-end


    def _CheckStepFile(self):
        NewStep = False

        if not self.SteppingDone:

            StepStatus = adios2.StepStatus.OK
            #@effis-begin self.StepEngine--->self.StepGroup

            while True:
                StepStatus = self.StepEngine.BeginStep(kittie.Kittie.ReadStepMode, 0.0)

                if (StepStatus == adios2.StepStatus.OK):
                    NewStep = True
                    self.SecondLastFoundSim[0] = self.LastFoundSim[0]
                    varid = self.StepIO.InquireVariable("StepNumber")
                    self.StepEngine.Get(varid, self.LastFoundSim)
                    self.StepEngine.EndStep()
                else:
                    break

            if StepStatus == adios2.StepStatus.EndOfStream:
                self.SteppingDone = True
            elif StepStatus == adios2.StepStatus.OtherError:
                StepStatus = adios2.StepStatus.EndOfStream
            elif StepStatus != adios2.StepStatus.NotReady:
                print(StepStatus)
                raise ValueError("Something weird happened reading the step information")

            #@effis-end
        return NewStep



    @property
    def NotDone(self):
        NewStep = False

        if self.on and (self.rank == 0) and (not self.SteppingDone):
            NewStep = self._CheckStepFile()

        #@effis-begin self.engine--->"plotter"
        ReadStatus = self.engine.BeginStep(kittie.Kittie.ReadStepMode, 0.0)
        print(ReadStatus); sys.stdout.flush()
        #@effis-end

        self.DoPlot = True

        if ReadStatus == adios2.StepStatus.NotReady:
            if self.on and (self.rank == 0) and NewStep and (self.SecondLastFoundSim[0] > self.LastFoundData[0]):
                #@effis-begin self.DoneEngine--->"done"
                self.DoneEngine.BeginStep()
                self.DoneEngine.Put(self.vDone, self.SecondLastFoundSim)
                self.DoneEngine.EndStep()
                #@effis-end
            self.DoPlot = False

        elif ReadStatus != adios2.StepStatus.OK:
            if (self.rank == 0) and self.on:
                while not os.path.exists(self.LastStepFile):
                    continue
                with open(self.LastStepFile, 'r') as infile:
                    text = infile.read()
                last = int(text.strip())
                if NewStep or (last > self.LastFoundData[0]):
                    #@effis-begin self.DoneEngine--->"done"
                    self.DoneEngine.BeginStep()
                    self.DoneEngine.Put(self.vDone, np.array([last], dtype=np.int64))
                    self.DoneEngine.EndStep()
                    #@effis-end

            """
                if self.StepOpen:
                    self.StepEngine.Close()
            """

            self.DoPlot = False
            return False

        return True


    def _ScheduleReads(self, y="match-dimensions"):
        self.data['minmax'] = {}
        for name in ['_StepPhysical', '_StepNumber']:
            varid = self.io.InquireVariable(name)
            self.engine.Get(varid, self.data[name])

        variables = self.io.AvailableVariables()

        if y == "match-dimensions":
            for name in self.uMatches:
                varid = self.io.InquireVariable(name)
                varid.SetSelection([self.DimInfo['starts'], self.DimInfo['counts']])
                self.engine.Get(varid, self.data[name])
                self.data['minmax'][name] = {}
                self.data['minmax'][name]['min'] = float(variables[name]['Min'])
                self.data['minmax'][name]['max'] = float(variables[name]['Max'])
        else:
            for name, start, count in zip(self.uMatches, self.uStarts, self.uCounts):
                varid = self.io.InquireVariable(name)
                varid.SetSelection([start, count])
                self.engine.Get(varid, self.data[name])
                self.data['minmax'][name] = {}
                self.data['minmax'][name]['min'] = float(variables[name]['Min'])
                self.data['minmax'][name]['max'] = float(variables[name]['Max'])


    def GetPlotData(self, y="match-dimensions"):

        self._ScheduleReads(y=y)

        #@effis-begin self.engine--->"plotter"
        self.engine.EndStep()
        #@effis-end

        #self.outdir = os.path.join("images", "{1}-{0}".format(self.data['_StepNumber'][0], self.config['plotter']['plots']))
        self.outdir = os.path.join("images", str(self.data['_StepNumber'][0]), self.config['plotter']['plots'])
        if self.rank == 0:
            if not os.path.exists(self.outdir):
                os.makedirs(self.outdir)

        self.ReadComm.Barrier()
        self.LastFoundData = self.data['_StepNumber']
        #LastFoundStep[0] = LastFoundData[0]


    def StepDone(self):
        self.ReadComm.Barrier()
        if self.on and (self.rank == 0):
            #@effis-begin self.DoneEngine--->"done"
            self.DoneEngine.BeginStep()
            self.DoneEngine.Put(self.vDone, self.LastFoundData)
            self.DoneEngine.EndStep()
            #@effis-end


