#!/usr/bin/env python

from __future__ import absolute_import, division, print_function, unicode_literals

import sys
import adios2

import datetime
import time
import os
import copy
import numpy as np
import yaml
import subprocess
import yaml
import numpy as np


class Coupler(object):

    def __init__(self, groupname):
        self.BegunStepping = False
        self.opened = False
        self.FindStep = False
        self.groupname = groupname
        self.lockfile = False


    def UntilNonexistentRead(self, verify=3):
        redo = False
        exists = True
        while exists:
            exists = os.path.exists(self.writing)

        self.reading = Kittie.Touch(self.reading)
        for i in range(verify):
            if os.path.exists(self.writing):
                os.remove(self.reading)
                redo = True
                break
        if redo:
            self.UntilNonexistentRead(verify=verify)


    def UntilNonexistentWrite(self):
        self.writing = Kittie.Touch(self.writing)
        for name in self.AllReading:
            exists = True
            while exists:
                exists = os.path.exists(name)


    def ReleaseLock(self):
        if self.comm is not None:
            self.comm.Barrier()

        if self.rank == 0:
            if self.mode == adios2.Mode.Write:
                os.remove(self.writing)
            elif self.mode == adios2.Mode.Read:
                os.remove(self.reading)


    def AcquireLock(self):
        if self.rank == 0:
            if self.mode == adios2.Mode.Write:
                self.UntilNonexistentWrite()
            elif self.mode == adios2.Mode.Read:
                while not os.path.exists(self.filename):
                    continue
                self.UntilNonexistentRead()

        if self.comm is not None:
            self.comm.Barrier()


    def CoupleOpen(self):
        if self.lockfile:
            self.AcquireLock()
        if self.comm is not None:
            self.engine = self.io.Open(self.filename, self.mode, self.comm)
        else:
            self.engine = self.io.Open(self.filename, self.mode)
        if self.lockfile:
            self.ReleaseLock()
        self.opened = True


    def open(self, filename, mode, comm=None):

        if not self.BegunStepping:
            self.mode = mode
            self.CurrentStep = -1
            self.comm = comm

            if self.comm is not None:
                self.rank = self.comm.Get_rank()
            else:
                self.rank = 0

            if self.io.EngineType().lower() in Kittie.FileMethods:
                self.lockfile = True

        if not self.opened:
            if (self.groupname in Kittie.YamlEngineSettings) and ('filename' in Kittie.YamlEngineSettings[self.groupname]):
                self.filename = Kittie.YamlEngineSettings[self.groupname]['filename']
            else:
                self.filename = filename

            self.writing = self.filename + Kittie.writing
            self.reading = self.filename + Kittie.MyReading
            self.AllReading = []
            for name in Kittie.AllReading:
                self.AllReading += [self.filename + name]

            self.CoupleOpen()


    def FileSeek(self, found, step, timeout):
        CurrentStep = -1
        self.AcquireLock()
        if not self.opened:
            if self.comm is not None:
                self.engine = self.io.Open(self.filename, self.mode, self.comm)
            else:
                self.engine = self.io.Open(self.filename, self.mode)
            self.opened = True

        while True:
            status = self.engine.BeginStep(adios2.StepMode.Read, timeout)
            if status == adios2.StepStatus.OK:
                CurrentStep += 1
            else:
                break

            if CurrentStep == step:
                found = True
                self.CurrentStep += 1
                break

            self.engine.EndStep()

        self.ReleaseLock()
        if not found:
            self.engine.Close()
            self.opened = False
            self.io.RemoveAllVariables()
            self.io.RemoveAllAttributes()
            if not os.path.exists(self.filename + ".done"):
                status = adios2.StepStatus.NotReady

        return status


    def begin_step(self, step=None, timeout=0.0):
        found = False

        if self.mode == adios2.Mode.Write:
            if not self.opened:
                self.CoupleOpen()
            self.engine.BeginStep(adios2.StepMode.Append, timeout)
            status = adios2.StepStatus.OK

        elif self.mode == adios2.Mode.Read:
            if step is None:
                usestep = self.CurrentStep + 1
            else:
                usestep = step

            if self.lockfile:
                while not found:
                    status = self.FileSeek(found, usestep, timeout)
                    if (timeout > 0):
                        break
            else:
                if not self.opened:
                    self.CoupleOpen()
                status = self.engine.BeginStep(adios2.StepMode.Read, timeout)

        self.BegunStepping = True
        return status


    def AddStep(self):
        if (not self.FindStep) and ((self.groupname in Kittie.StepGroups) or Kittie.AllStep):
            self.FindStep = True
            if (self.mode == adios2.Mode.Write) and (self.rank == 0):
                self.io.DefineVariable("_StepNumber", Kittie.StepNumber, [], [], [])
                self.io.DefineVariable("_StepPhysical", Kittie.StepPhysical, [], [], [])

        if self.FindStep and (self.mode == adios2.Mode.Write) and (self.rank == 0):
            vNumber = self.io.InquireVariable("_StepNumber")
            vPhysical = self.io.InquireVariable("_StepPhysical")
            self.engine.Put(vNumber, Kittie.StepNumber)
            self.engine.Put(vPhysical, Kittie.StepPhysical)


    def end_step(self):
        self.AddStep()
        if self.lockfile:
            self.AcquireLock()
        self.engine.EndStep()
        if self.lockfile:
            self.ReleaseLock()
            if self.mode == adios2.Mode.Read:
                self.engine.Close()
                self.opened = False
                self.io.RemoveAllVariables()
                self.io.RemoveAllAttributes()


    def close(self):
        if self.opened:
            if (self.mode == adios2.Mode.Write) and self.lockfile:
                self.AcquireLock()
            self.engine.Close()
            self.opened = False
            if (self.mode == adios2.Mode.Write) and self.lockfile:
                self.ReleaseLock()


class Kittie(object):
    writing = ".writing"
    reading = ".reading"
    touch = False
    #appname = None
    Codename = None

    # The namespace you init into
    adios = None
    FileMethods = ["bpfile", "bp", "bp3", "hdf5"]

    # Getting the list of all group names found in pre-processing doesn't seem to be needed
    #CompileGroups = []

    YamlEngineSettings = {}
    StepGroups = []
    AllReading = []
    Couplers = {}


    #######################
    """ Utility methods """
    #######################

    @classmethod
    def Touch(cls, name):
        if cls.touch:
            subprocess.call(["touch", name])
        else:
            try:
                open(name, "w").close()
            except:
                #name = os.path.relpath(os.path.realpath(name), os.getcwd())
                name = os.path.relpath(os.path.basename(name))
                open(name, "w").close()
        return name


    ##############################
    """ Initialization Related """
    ##############################

    """
    @classmethod
    def BuildYaml(cls):
        num = os.environ["KITTIE_NUM"]
        yamlfile = os.environ["KITTIE_YAML_FILE"]
        if os.path.exists(yamlfile):
            with open(yamlfile, 'r') as ystream:
                config = yaml.load(ystream)
            #cls.appname = config["appname"] + "-" + num
            #cls.CompileGroups = config["groups"]
    """


    @classmethod
    def GroupsYaml(cls):
        #yamlfile = ".kittie-groups-" + cls.appname + ".yaml"
        yamlfile = ".kittie-groups-" + os.environ["KITTIE_NUM"] + ".yaml"
        if os.path.exists(yamlfile):
            with open(yamlfile, 'r') as ystream:
                cls.YamlEngineSettings = yaml.load(ystream)
            for name in cls.YamlEngineSettings:
                if cls.YamlEngineSettings[name]["AddStep"]:
                    cls.StepGroups += [name]


    @classmethod
    def CodesYaml(cls, readid=""):
        yamlfile =  ".kittie-codenames-" + os.environ["KITTIE_NUM"] + ".yaml"
        if os.path.exists(yamlfile):
            with open(yamlfile, 'r') as ystream:
                config = yaml.load(ystream)
            cls.Codename = config["codename"]
            cls.MyReading = cls.reading + "-" + cls.Codename + readid
            for name in config["codes"]:
                cls.AllReading += [cls.reading + "-" + name + readid]
        else:
            cls.Codename = "unknown"
            cls.MyReading = cls.reading
            cls.AllReading = [cls.MyReading]


    @classmethod
    def Initialize(cls, comm=None, xml=None, readid=""):

        # Need a communicator in both the Kittie (global space) and each group
        cls.comm = comm
        if cls.comm is not None:
            cls.rank = cls.comm.Get_rank()
            cls.comm_self = cls.comm.Split(cls.rank, cls.rank)
        else:
            cls.rank = 0
            cls.comm_self = None

        args = []
        if xml is not None:
            args += [xml]
        if cls.comm is not None:
            args += [cls.comm]
        args += [adios2.DebugON]
        cls.adios = adios2.ADIOS(*args)

        cls.AllStep = False
        #cls.AllStep = True
        #cls.BuildYaml()
        cls.GroupsYaml()
        cls.CodesYaml(readid=readid)

        cls.StepInit = False
        #cls.StepGroupname = cls.appname + "-step"
        cls.StepGroupname = cls.Codename + "-step"


    @classmethod
    def Finalize(cls):
        for name in cls.Couplers.keys():
            filename = cls.Couplers[name].filename + ".done"
            cls.Couplers[name].close()
            if (cls.rank == 0) and (cls.Couplers[name].mode == adios2.Mode.Write):
                filename = cls.Touch(filename)

        if cls.StepInit:
            with open(cls.StepGroupname + ".done", "w") as outfile:
                outfile.write("{0}".format(Kittie.StepNumber[0]))
            cls.StepEngine.Close()



    ############################
    """ ADIOS-like statments """
    ############################

    @classmethod
    def declare_io(cls, groupname):
        io = cls.adios.DeclareIO(groupname)
        cls.Couplers[groupname] = Coupler(groupname)
        if groupname in cls.YamlEngineSettings:
            entry = cls.YamlEngineSettings[groupname]
            if 'engine' in entry:
                io.SetEngine(entry['engine'])
            if 'params' in entry:
                for key in entry['params']:
                    io.SetParameter(key, entry['params'][key])
        cls.Couplers[groupname].io = io
        return io


    @classmethod
    def open(cls, groupname, filename, mode, comm=None):
        """
        if comm is not None:
            cls.Couplers[groupname].open(filename, mode, comm=comm)
        else:
            cls.Couplers[gropuname].open(filename, mode, comm=cls.comm)
        """
        cls.Couplers[groupname].open(filename, mode, comm=comm)

        return cls.Couplers[groupname].engine


    @classmethod
    def write_step(cls, physical, number, comm=None):
        if cls.rank == 0:
            cls.StepPhysical = np.array([physical], dtype=np.float64)
            cls.StepNumber = np.array([number], dtype=np.int64)
            if not cls.StepInit:
                cls.StepIO = cls.adios.DeclareIO(cls.StepGroupname)
                cls.StepIO.DefineVariable("StepNumber", cls.StepNumber, [], [], [])
                cls.StepIO.DefineVariable("StepPhysical", cls.StepPhysical, [], [], [])
                cls.StepIO.SetEngine("SST")
                cls.StepIO.SetParameter("RendezvousReaderCount", "0")
                cls.StepIO.SetParameter("QueueLimit", "1")
                cls.StepIO.SetParameter("QueueFullPolicy", "Discard")
                if cls.comm_self is None:
                    cls.StepEngine = cls.StepIO.Open(cls.StepGroupname, adios2.Mode.Write)
                else:
                    cls.StepEngine = cls.StepIO.Open(cls.StepGroupname, adios2.Mode.Write, cls.comm_self)
                cls.StepInit = True

            cls.StepEngine.BeginStep()
            vNumber = cls.StepIO.InquireVariable("StepNumber")
            vPhysical = cls.StepIO.InquireVariable("StepPhysical")
            cls.StepEngine.Put(vNumber, cls.StepNumber)
            cls.StepEngine.Put(vPhysical, cls.StepPhysical)
            cls.StepEngine.EndStep()

